"""Downloads libmpv (mpv-2.dll) and FFmpeg (ffmpeg.exe, ffprobe.exe) for Windows.
Run by setup.bat. Safe to run again: it skips what already exists."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"
MPV_DLL = ROOT / "mpv-2.dll"
VULKAN_DLL = ROOT / "vulkan-1.dll"

MPV_RELEASES_API = "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"
FFMPEG_ZIP_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
# Official standalone 7-Zip console extractor (py7zr cannot unpack the BCJ2 filter libmpv uses)
SEVENZIP_URL = "https://www.7-zip.org/a/7zr.exe"
# libmpv imports vulkan-1.dll at load time. Sources of the Vulkan loader, tried in order:
VULKAN_URLS = [
    # official LunarG Vulkan Runtime (Apache-2.0)
    "https://sdk.lunarg.com/sdk/download/1.4.357.0/windows/VulkanRT-X64-1.4.357.0-Components.zip",
    # fallback: the same loader, bundled with libmpv by the SubtitleEdit project
    "https://github.com/SubtitleEdit/support-files/releases/download/libmpv-2026-08-14b/libmpv2-win64.zip",
]
HEADERS = {"User-Agent": "pedio-editor-setup"}

# The Windows certificate store can miss root certificates (CERTIFICATE_VERIFY_FAILED).
# Use certifi's CA bundle instead (same one pip uses).
try:
    import certifi

    os.environ["SSL_CERT_FILE"] = certifi.where()
except ImportError:
    pass


def fetch(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done * 100 // total}% ({done // 1048576} MB)", end="", flush=True)
    print()


def find_7zip(tmp: Path) -> str:
    """Use an installed 7-Zip if there is one, otherwise download the small 7zr.exe."""
    for name in ("7z", "7zr"):
        found = shutil.which(name)
        if found:
            return found
    for path in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if os.path.exists(path):
            return path
    exe = tmp / "7zr.exe"
    print("[mpv] Downloading 7zr.exe (small 7-Zip extractor)")
    fetch(SEVENZIP_URL, exe)
    return str(exe)


def setup_mpv() -> None:
    if MPV_DLL.exists():
        print("[mpv] mpv-2.dll already exists - skipping")
        return

    print("[mpv] Finding latest libmpv build...")
    req = urllib.request.Request(MPV_RELEASES_API, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        release = json.load(r)

    asset = None
    for a in release["assets"]:
        n = a["name"]
        # plain x86_64 dev build (not the -v3 or i686 variants)
        if n.startswith("mpv-dev-x86_64-") and "-v3-" not in n and n.endswith(".7z"):
            asset = a
            break
    if asset is None:
        sys.exit("[mpv] Could not find the libmpv download. Get it manually (see README).")

    # ignore_cleanup_errors: on Windows, antivirus can hold a just-downloaded file for a moment
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        archive = tmp / asset["name"]
        print(f"[mpv] Downloading {asset['name']}")
        fetch(asset["browser_download_url"], archive)

        sevenzip = find_7zip(tmp)
        out = tmp / "out"
        # e = extract without folders, only *.dll files, searched recursively
        result = subprocess.run(
            [sevenzip, "e", str(archive), f"-o{out}", "*.dll", "-r", "-y"],
            capture_output=True, text=True, errors="replace",
        )
        if result.returncode != 0:
            sys.exit("[mpv] Extraction failed:\n" + result.stdout + result.stderr)

        dlls = [p for p in out.glob("*.dll") if "mpv" in p.name.lower()]
        if not dlls:
            sys.exit("[mpv] No mpv dll found in the archive.")
        shutil.copy2(dlls[0], MPV_DLL)
    print("[mpv] Installed mpv-2.dll")


def pe_machine(path: Path) -> int:
    """CPU type of a Windows dll: 0x8664 = 64-bit x64, 0x14C = 32-bit."""
    with open(path, "rb") as f:
        head = f.read(4096)
    offset = int.from_bytes(head[60:64], "little")
    return int.from_bytes(head[offset + 4:offset + 6], "little")


def setup_vulkan() -> None:
    """libmpv needs vulkan-1.dll to load. PCs without a Vulkan graphics driver (older Intel
    graphics, basic display driver) don't have it, so put the loader next to mpv-2.dll.
    It is only the loader: with no Vulkan driver it finds no devices and mpv uses Direct3D 11."""
    if VULKAN_DLL.exists():
        print("[vulkan] vulkan-1.dll already exists - skipping")
        return
    if (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "vulkan-1.dll").exists():
        print("[vulkan] already installed in Windows\\System32 - skipping")
        return

    for url in VULKAN_URLS:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp = Path(tmp)
            print(f"[vulkan] Downloading {url.rsplit('/', 1)[-1]}")
            try:
                fetch(url, tmp / "vulkan.zip")
                with zipfile.ZipFile(tmp / "vulkan.zip") as z:
                    for name in z.namelist():
                        if name.rsplit("/", 1)[-1].lower() != "vulkan-1.dll":
                            continue
                        candidate = tmp / "vulkan-1.dll"
                        candidate.write_bytes(z.read(name))
                        if pe_machine(candidate) == 0x8664:  # skip the 32-bit copy if the zip has both
                            shutil.copy2(candidate, VULKAN_DLL)
                            print("[vulkan] Installed vulkan-1.dll")
                            return
                print("[vulkan] no 64-bit vulkan-1.dll in that download - trying next source")
            except Exception as e:  # network error, 404, bad zip ...
                print(f"[vulkan] failed ({e}) - trying next source")
    sys.exit(
        "[vulkan] Could not download vulkan-1.dll.\n"
        "Install the 'Vulkan Runtime' from https://vulkan.lunarg.com/sdk/home#windows "
        "and run setup.bat again."
    )


def setup_ffmpeg() -> None:
    if (BIN / "ffmpeg.exe").exists() and (BIN / "ffprobe.exe").exists():
        print("[ffmpeg] already in bin\\ - skipping")
        return
    BIN.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        zip_path = Path(tmp) / "ffmpeg.zip"
        print("[ffmpeg] Downloading FFmpeg (about 90 MB)...")
        fetch(FFMPEG_ZIP_URL, zip_path)
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                base = name.rsplit("/", 1)[-1]
                if base in ("ffmpeg.exe", "ffprobe.exe"):
                    with z.open(name) as src, open(BIN / base, "wb") as dst:
                        shutil.copyfileobj(src, dst)
    if not (BIN / "ffmpeg.exe").exists():
        sys.exit("[ffmpeg] ffmpeg.exe not found in the download.")
    print("[ffmpeg] Installed to bin\\")


if __name__ == "__main__":
    setup_mpv()
    setup_vulkan()
    setup_ffmpeg()
