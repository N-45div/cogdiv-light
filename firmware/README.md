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
2. Plug the board into the laptop. In **Device Manager → Ports (COM & LPT)** it shows as
   **USB Serial Device (COMx)**: the ESP32-S3's own USB, no driver needed. If it doesn't show,
   hold **BOOT**, press and release **RST**, release **BOOT**.
3. Open **https://espressif.github.io/esptool-js/** (Espressif's official web flasher).
4. Baudrate **921600** → **Connect** → choose the board's COM port.
   *If it won't connect:* hold **BOOT**, press and release **RST**, release **BOOT**, try again.
5. Flash address **0x0** (the page suggests 0x1000: change it), choose
   `windbuddy-explorer.bin`, click **Program once** (a double click starts two writes that
   corrupt each other). Wait for *Hash of data verified*.
6. Press **RST** (or unplug and replug).

**Quickest: flash and set up in one command, no phone.** It asks for the Wi-Fi name, Wi-Fi
password and device token at hidden prompts and writes them to the board with the firmware
(about 5 MB of Python tools, no compiler). If the Wi-Fi details are wrong, the board falls back
to the phone setup hotspot.

```powershell
py -m pip install esptool esp-idf-nvs-partition-gen
py firmware/provision.py            # hold BOOT, tap RST, release BOOT when it says so
```

**Or from a terminal** (Python), with the board in flashing mode (hold BOOT, tap RST):

```powershell
py -m pip install esptool
py -m esptool --chip esp32s3 --port COM8 --baud 460800 write_flash 0x0 firmware/dist/windbuddy-explorer.bin
```

**The COM port disappears once Wind Buddy runs.** The board's USB uses GPIO 19/20, which the
screen also uses, so USB stops when the screen starts. That's expected: updates and logs go
through the server from then on (section 5). To flash by USB again, hold BOOT and tap RST first.

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

**Deployed (normal):** use the URL that `modal deploy deploy/modal_app.py` printed, e.g.
`https://<workspace>--wind-buddy-web.modal.run`, and the device token for `desk-1` from the
`DEVICE_TOKENS` you set in the `wind-buddy` Modal secret. Commands for the deployed app, including the
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

## 5. Checking and updating it remotely

Once the board is on Wi-Fi it polls the server, so it can be checked, debugged and updated from
anywhere, no laptop connection needed (`URL` = server, `APP` = app token):

```bash
curl URL/api/devices                       # last seen, Wi-Fi signal, firmware, free memory,
                                           # last HTTP status, screen mode, button presses,
                                           # relay pin, app slot, and the last 20 log lines
curl URL/api/device/desk-1/logs            # the board's log: boot, Wi-Fi, screen and relay
                                           # changes, presses, errors, updates
curl -X POST URL/api/device/desk-1/test -H "X-App-Token: APP" \
     -H "Content-Type: application/json" -d '{"relay": true, "seconds": 5}'
                                           # relay on for 5 s: hear it click, see "Relay test"
curl URL/api/firmware                      # the firmware this server can install
curl -X POST URL/api/device/desk-1/update -H "X-App-Token: APP" \
     -H "Content-Type: application/json" -d '{"version": "0.3.0"}'
                                           # install it over the air ({"cancel": true} withdraws)
```

The board reports telemetry every 30 seconds and right after each button press; log lines
travel with it.

**Over-the-air updates** (from 0.3.0): the server offers the image in `dist/` that was deployed
with it. The board downloads it (screen: *Updating*, with a progress bar; the load is switched
off), checks its MD5, and restarts into it. The new firmware runs **on trial**: it keeps itself
once it reaches the server; if it can't within 3 minutes, or crashes, the board goes back to
the previous firmware by itself and won't retry that image. So a bad update can't cut the board
off. Updates need 0.3.0 or later on the board, so the first 0.3.0 goes on by USB (section 1).

## 6. Building it yourself

```bash
pip install platformio
cd firmware
pio run                 # builds, and writes dist/: windbuddy-explorer.bin (USB, at 0x0),
                        # windbuddy-app.bin + manifest.json (over the air)
pio run -t upload       # flash over USB
pio device monitor      # serial log at 115200
```

Pinned versions: platform `espressif32@7.1.3` (Arduino core 2.0.17), Adafruit ST7735 1.11.0,
Adafruit GFX 1.12.6, ArduinoJson 7.4.3, WiFiManager 2.0.17.

**Known trade-off:** over `https://` the server certificate isn't checked
(`setInsecure()`), to keep setup simple for the hackathon. Pin a root CA before real use.
