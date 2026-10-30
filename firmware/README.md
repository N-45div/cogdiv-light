# Wind Buddy firmware (ESP32-S3)

For the **OceanLabz ESP32-S3 AI Explorer camera board** (N16R8) with its 1.8" ST7735 screen.
The device shows the agent's charging plan, asks *hold: yes / tap: no* on the BOOT button,
and switches a load (relay on GPIO 14) during the planned windows. The server decides
everything, including the screen text; the device polls it every 5 seconds.

| Part | Pins (from OceanLabz's own firmware config, see `include/board.h`) |
|---|---|
| Screen ST7735 128x160 | CS 45, DC 47, RST 21, CLK 19, MOSI 20, backlight 38 |
| Button | BOOT (GPIO 0) |
| Relay / MOSFET input | GPIO 14 (on = HIGH) |

## 1. Flash it from the browser (Windows, no installs)

You need Chrome or Edge and a USB-C cable that carries data.

1. **Download** [`dist/windbuddy-explorer.bin`](dist/windbuddy-explorer.bin) (on GitHub: open
   the file, then *Download raw file*).
2. Plug the board into the laptop. In **Device Manager → Ports (COM & LPT)** a new COM port
   should appear. If none does, install the USB-serial driver for the chip on the board
   (usually CH343/CH340 or CP210x) and plug it in again.
3. Open **https://espressif.github.io/esptool-js/** (Espressif's official web flasher).
4. Baudrate **921600** → **Connect** → choose the board's COM port.
   *If it won't connect:* hold **BOOT**, press and release **RST**, release **BOOT**, try again.
5. Flash address **0x0**, choose `windbuddy-explorer.bin`, click **Program**. About 30 seconds.
6. Press **RST** (or unplug and replug).

This replaces the XiaoZhi firmware that came on the board. To go back, use OceanLabz's
web installer on the board's tutorial page.

## 2. First start: Wi-Fi and server, from your phone

The screen says **Wi-Fi setup**.

1. On your phone, join Wi-Fi **WindBuddy-Setup**, password **windsetup**.
2. A setup page opens (if not, browse to **192.168.4.1**) → **Configure WiFi**.
3. Pick your home Wi-Fi (**2.4 GHz** only), enter its password, and fill in:
   - **Server URL**: where the API runs (see below), e.g. `http://192.168.1.20:8000`
   - **Device id**: `desk-1`
   - **Device token**: only if the server sets `DEVICE_TOKENS`
   - **Screen rotation**: `0` (try 2 if the picture is upside down)
4. **Save**. The board joins your Wi-Fi and shows **Wind now**.

To change any of this later: hold **BOOT** for 10 seconds.

## 3. The server

**Deployed (normal):** use the URL from the latest *Deploy* run on GitHub (Actions → Deploy →
summary), e.g. `https://<workspace>--wind-buddy-web.modal.run`, and the device token for
`desk-1` from the owner's `DEVICE_TOKENS` secret. Commands for the deployed app, including the
replay demo, are in [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md).

**Without the deployment**, run the API on the laptop and use the laptop's address:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
$env:CLOCK_MODE = "replay"            # or "live"
$env:MODEL_API_KEY = "..."            # your Meta Model API key, for the agent
uvicorn wind_monitor.main:app --host 0.0.0.0 --port 8000
```

Find the laptop's address with `ipconfig` (IPv4 Address, e.g. 192.168.1.20) and use
`http://192.168.1.20:8000` as the Server URL. Allow Python through the Windows firewall on
private networks when asked.

Then send it a plan, either from the API's own page at **http://localhost:8000/api/docs**
(`POST /api/agent/ask` → *Try it out*) or from PowerShell:

```powershell
Invoke-RestMethod localhost:8000/api/agent/ask -Method Post -ContentType "application/json" `
  -Body '{"question": "My EV needs 21 kWh at 7 kW by 07:00. When should it charge?", "device_id": "desk-1"}'
```

The screen shows **Charge plan?** → **hold BOOT** to approve, **tap** to reject. In replay
mode, fast-forward and watch the relay follow the plan (900 = 15 minutes per second):

```powershell
Invoke-RestMethod localhost:8000/api/clock -Method Post -ContentType "application/json" -Body '{"speed": 900}'
```

## 4. Using the device

| Screen | Tap | Hold (1 s) |
|---|---|---|
| Charge plan? | reject | approve |
| Waiting for wind / CHARGING | refresh | stop the plan |
| Wind now / Charge done | refresh | (nothing) |
| any | | 10 s: Wi-Fi and server setup |

The **LOAD ON / load off** box and the board's RGB LED (green) show the relay. If the server
can't be reached for 10 minutes, the relay switches off.

**Wiring the load (low voltage only):** relay module IN → GPIO 14, VCC → 5V, GND → GND; the
relay switches a USB lamp or fan. Don't switch mains.

## 5. Building it yourself

```bash
pip install platformio
cd firmware
pio run                 # builds, and writes dist/windbuddy-explorer.bin
pio run -t upload       # flash over USB
pio device monitor      # serial log at 115200
```

Pinned versions: platform `espressif32@7.1.3` (Arduino core 2.0.17), Adafruit ST7735 1.11.0,
Adafruit GFX 1.12.6, ArduinoJson 7.4.3, WiFiManager 2.0.17.

**Known trade-off:** over `https://` the server certificate isn't checked
(`setInsecure()`), to keep setup simple for the hackathon. Pin a root CA before real use.
