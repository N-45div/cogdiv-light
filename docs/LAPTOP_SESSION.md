# Handoff: the laptop session (device side)

You are a Claude Code session on the owner's **Windows laptop**, with an **OceanLabz ESP32-S3
AI Explorer camera board** plugged in over USB. Your job: get the Wind Buddy firmware running on
that board and working end to end with the deployed server. A separate cloud session owns the
server; you own the device.

## Ground rules

- **Only change files in `firmware/`.** The cloud session changes `backend/`, `deploy/`,
  `docs/` and the workflows. If the server needs a change, tell the owner what and why.
- Work on `main`. `git pull` before you start and before every push. Small commits, each with a
  clear message.
- **Commit as the repo owner. Do not add `Co-Authored-By` or `Claude-Session` lines** to commit
  messages (the owner's instruction).
- Never commit Wi-Fi passwords, tokens or keys. They are entered on the device's setup page.

## Read first

1. `firmware/README.md`: pins, flashing, the phone setup page, using the device.
2. `docs/DECISIONS.md` section **C10**: how the device and server split the work.
3. `backend/wind_monitor/device_api.py`: the two endpoints the device calls.

## What you need from the owner

- **Server URL**: from the latest *Deploy* run on GitHub (Actions → Deploy → summary), like
  `https://<workspace>--wind-buddy-web.modal.run`. No trailing slash.
- **Device token** for `desk-1` (part of their `DEVICE_TOKENS` secret) and the **app token**
  (`APP_TOKEN`), for the test commands below.

## Steps

1. **Tools:** Python 3.12, then `pip install platformio==6.2.0`. The first build downloads the
   ESP32 toolchain (a few minutes).
2. **Find the board:** `pio device list`. No COM port? Device Manager shows the USB-serial chip;
   install its driver (CH343/CH340 or CP210x) and replug.
3. **Build and flash:** `cd firmware`, then `pio run -t upload --upload-port COMx`.
   Won't connect: hold **BOOT**, tap **RST**, release **BOOT**, retry.
4. **Serial log:** `pio device monitor -p COMx` (115200). Expect `Wind Buddy 0.1.0`.
   HTTP errors are logged as `GET /api/device/desk-1/state -> HTTP <code>`.
5. **Setup (owner, on a phone):** join Wi-Fi `WindBuddy-Setup` (password `windsetup`), pick the
   home 2.4 GHz Wi-Fi, enter Server URL, device id `desk-1`, the device token, rotation `0`.
6. **Check the screen:** *Wind now* with a GW figure, updated every few seconds.
7. **End to end:** send a plan (PowerShell; URL and APP from the owner):
   ```powershell
   Invoke-RestMethod URL/api/agent/ask -Method Post -Headers @{"X-App-Token"="APP"} `
     -ContentType "application/json" `
     -Body '{"question": "My EV needs 21 kWh at 7 kW by 07:00. When should it charge?", "device_id": "desk-1"}'
   ```
   Screen: **Charge plan?** → hold BOOT 1 s → **Waiting for wind** (tap would reject).
8. **Relay, fast:** `docs/DEPLOYMENT.md` has the replay commands (jump to 18:00 on 20 Jan, ask
   for a plan, approve, speed 900). The relay output (GPIO 14) and the green LED come on at the
   planned start and go off at the end.
9. **Wiring the load** (owner, low voltage only): relay module IN → GPIO 14, VCC → 5V, GND → GND;
   the relay switches a USB lamp or fan.
10. **Telemetry:** `Invoke-RestMethod URL/api/devices` lists `desk-1` with `rssi` and `firmware`.

## Troubleshooting

| Symptom | Likely cause | Try |
|---|---|---|
| Backlight on, screen blank or noisy | panel variant | in `src/main.cpp` change `INITR_BLACKTAB` to `INITR_GREENTAB` or `INITR_REDTAB` |
| Picture upside down / mirrored | mounting | rotation on the setup page (hold BOOT 10 s): try 2 |
| Colours wrong (red/blue swapped) | panel variant | `INITR_GREENTAB` |
| *No connection*, log shows 401 | wrong device token | setup page |
| *No connection*, log shows -1 or 404 | wrong URL | setup page: `https://...` with no trailing slash |
| Boot loop | flash settings | serial log; reflash; report the log |

## When done

Add a **"Tested on hardware"** section to `firmware/README.md`: date, what worked, what you
changed and why. Commit and push (pull first). Tell the owner anything the server should change.
