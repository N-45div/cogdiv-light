"""Flash Wind Buddy and set it up over USB in one go: no phone, no setup hotspot.

    py firmware/provision.py                 # asks for Wi-Fi name, Wi-Fi password, device token
    py firmware/provision.py --port COM8 --server https://... --device desk-1

Needs only `pip install esptool esp-idf-nvs-partition-gen` (a few MB, no compiler).

It writes the settings the phone setup page would have saved into the board's settings area
(the NVS partition at 0x9000), spliced into the merged firmware image, so one flash does both:
- namespace "windbuddy": server, device, token, rotation (read by firmware/src/main.cpp);
- namespace "nvs.net80211": the Wi-Fi name and password, where the ESP32's Wi-Fi stack keeps
  them, so the board joins Wi-Fi straight away. If they're wrong, the board opens the usual
  "WindBuddy-Setup" hotspot instead, so nothing is lost.

Secrets are typed at hidden prompts, written only to a temporary folder that is deleted
afterwards, and never printed.

Put the board in flashing mode first: hold BOOT, tap RST, release BOOT. (Once Wind Buddy runs,
its screen takes over the USB pins and the COM port disappears: that's expected.)
"""

from __future__ import annotations

import argparse
import getpass
import os
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGE = HERE / "dist" / "windbuddy-explorer.bin"
NVS_OFFSET, NVS_SIZE = 0x9000, 0x5000  # default_16MB.csv
DEFAULT_SERVER = "https://ndivij2004--wind-buddy-web.us-east.modal.run"
ESPRESSIF_VID = 0x303A


def find_port(wait_s: float) -> str:
    from serial.tools import list_ports

    deadline = time.monotonic() + wait_s
    while True:
        for port in list_ports.comports():
            if port.vid == ESPRESSIF_VID:
                return port.device
        if time.monotonic() > deadline:
            sys.exit("No ESP32-S3 on USB. Plug it in, hold BOOT, tap RST, release BOOT.")
        time.sleep(1)


def esptool(*args: str) -> int:
    return subprocess.call([sys.executable, "-m", "esptool", "--chip", "esp32s3", *args])


def wait_for_flashing_mode(port: str, wait_s: float) -> None:
    """Probe without resetting until the ROM loader answers (after BOOT+RST)."""
    deadline = time.monotonic() + wait_s
    print(f"Waiting for the board on {port} to be in flashing mode (hold BOOT, tap RST)...")
    while time.monotonic() < deadline:
        probe = subprocess.run(
            [sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", port,
             "--before", "no-reset", "--after", "no-reset", "--connect-attempts", "1", "chip-id"],
            capture_output=True, text=True,
        )
        if probe.returncode == 0:
            print("Board is in flashing mode.")
            return
        time.sleep(2)
    sys.exit("The board never answered. Hold BOOT, tap RST, release BOOT, and run this again.")


def wifi_blobs(ssid: str, password: str) -> tuple[bytes, bytes]:
    """The ESP32 Wi-Fi stack's own records: wifi_ssid_t (len + 32 bytes) and a 65-byte password."""
    raw_ssid, raw_pass = ssid.encode(), password.encode()
    if not 1 <= len(raw_ssid) <= 32:
        sys.exit("Wi-Fi name must be 1-32 bytes")
    if len(raw_pass) > 64 or 0 < len(raw_pass) < 8:
        sys.exit("Wi-Fi password must be 8-64 characters (or empty for an open network)")
    return struct.pack("<I32s", len(raw_ssid), raw_ssid), raw_pass.ljust(65, b"\0")


def build_image(folder: Path, settings: dict, wifi: tuple[str, str] | None) -> Path:
    def esc(value: str) -> str:
        if "," in value or "\n" in value or '"' in value:
            sys.exit(f"Setting can't contain commas, quotes or newlines: {value[:10]}...")
        return value

    rows = [
        "key,type,encoding,value",
        "windbuddy,namespace,,",
        f"server,data,string,{esc(settings['server'])}",
        f"device,data,string,{esc(settings['device'])}",
        f"rotation,data,i32,{settings['rotation']}",
    ]
    if settings["token"]:
        rows.append(f"token,data,string,{esc(settings['token'])}")
    if wifi:
        # The generator knows these two keys and adds the records the Wi-Fi stack expects
        # with them (sta.apinfo, sta.pmk, sta.apsw); it takes them as hex data, not files.
        ssid_blob, pass_blob = wifi_blobs(*wifi)
        rows += [
            "nvs.net80211,namespace,,",
            f"sta.ssid,data,hex2bin,{ssid_blob.hex()}",
            f"sta.pswd,data,hex2bin,{pass_blob.hex()}",
        ]
    (folder / "settings.csv").write_text("\n".join(rows) + "\n")
    nvs = folder / "nvs.bin"
    done = subprocess.run(
        [sys.executable, "-m", "esp_idf_nvs_partition_gen", "generate",
         str(folder / "settings.csv"), str(nvs), hex(NVS_SIZE)],
        capture_output=True, text=True,
    )
    if done.returncode != 0 or not nvs.exists():
        sys.exit("Couldn't build the settings image:\n" + done.stdout[-500:] + done.stderr[-500:])

    firmware = IMAGE.read_bytes()
    data = nvs.read_bytes()
    assert len(data) == NVS_SIZE and len(firmware) > NVS_OFFSET + NVS_SIZE
    combined = folder / "windbuddy-with-settings.bin"
    combined.write_bytes(
        firmware[:NVS_OFFSET] + data + firmware[NVS_OFFSET + NVS_SIZE :]
    )
    return combined


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", help="e.g. COM8 (default: the first Espressif USB device)")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--device", default="desk-1")
    parser.add_argument("--rotation", type=int, default=0, choices=range(4))
    parser.add_argument("--ssid", help="Wi-Fi name (asked if not given; 2.4 GHz only)")
    parser.add_argument("--no-wifi", action="store_true", help="leave Wi-Fi to the phone setup")
    parser.add_argument("--baud", type=int, default=460800)
    args = parser.parse_args()

    if not IMAGE.exists():
        sys.exit(f"Missing {IMAGE}: run `pio run` in firmware/ or git pull")
    wifi = None
    if not args.no_wifi:
        ssid = args.ssid or input("Wi-Fi name (2.4 GHz): ").strip()
        wifi = (ssid, getpass.getpass("Wi-Fi password (hidden): "))
    token = os.environ.get("WINDBUDDY_TOKEN") or getpass.getpass(
        "Device token (hidden; Enter if the server uses none): "
    )
    settings = {
        "server": args.server.strip().rstrip("/"),
        "device": args.device,
        "token": token.strip(),
        "rotation": args.rotation,
    }

    port = args.port or find_port(120)
    wait_for_flashing_mode(port, 300)
    with tempfile.TemporaryDirectory(prefix="windbuddy-") as tmp:
        image = build_image(Path(tmp), settings, wifi)
        for baud in (args.baud, 115200):
            code = esptool("--port", port, "--baud", str(baud), "--before", "no-reset",
                           "--after", "hard-reset", "write-flash", "0x0", str(image))
            if code == 0:
                break
            print(f"Flash failed at {baud} baud; retrying slower...")
        else:
            sys.exit("Flashing failed. Hold BOOT, tap RST, release BOOT, and run this again.")

    print(
        f"\nDone. The board restarts, joins Wi-Fi and talks to {settings['server']} as "
        f"{settings['device']}.\nCheck: curl {settings['server']}/api/device/{settings['device']}"
        "/logs\nIf the screen shows 'Wi-Fi setup' instead, the Wi-Fi name or password didn't "
        "work: finish on the phone."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
