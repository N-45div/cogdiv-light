# Deployment (Modal, from GitHub)

Every push to `main` that passes CI is deployed to Modal by `.github/workflows/deploy.yml`.
The app runs as **one always-on container** (`deploy/modal_app.py`): it serves the API and the
website at `https://<your-modal-workspace>--wind-buddy-web.modal.run`.

## One-time setup (owner)

GitHub → repository **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | What | Needed for |
|---|---|---|
| `MODAL_TOKEN_ID` | Modal token id, starts `ak-` (modal.com → Settings → API Tokens) | deploying |
| `MODAL_TOKEN_SECRET` | Modal token secret, starts `as-` (shown once, with the id) | deploying |
| `MODEL_API_KEY` | Meta Model API key | the agent (Muse Spark) |
| `BASETEN_API_KEY` | Baseten key | backup model (Muse Spark Open) |
| `DEVICE_TOKENS` | `desk-1:<random>` (more devices: comma-separated) | devices must send their token |
| `APP_TOKEN` | `<random>` | asking the agent, moving the clock, proposing to a device |
| `FIREWORKS_API_KEY` | optional | |

Optional repository **variable** (same page, *Variables* tab): `CLOCK_MODE` = `live` (default)
or `replay`.

A random token, in PowerShell:
`-join ((48..57) + (97..122) | Get-Random -Count 24 | ForEach-Object {[char]$_})`

Then **Actions → Deploy → Run workflow** (or push to main). The run's summary shows the URL.
Secrets never appear in the repo or the logs; the workflow copies them into the Modal secret
`wind-buddy` on each deploy.

## Using the deployed app

Replace `URL`, `APP` (your `APP_TOKEN`) below.

```powershell
# Is it up? What time does it think it is?
Invoke-RestMethod URL/api/clock

# Ask the agent and send its plan to the device
Invoke-RestMethod URL/api/agent/ask -Method Post -Headers @{"X-App-Token"="APP"} `
  -ContentType "application/json" `
  -Body '{"question": "My EV needs 21 kWh at 7 kW by 07:00. When should it charge?", "device_id": "desk-1"}'

# Demo: replay a January evening at 15 minutes per second
Invoke-RestMethod URL/api/clock -Method Post -Headers @{"X-App-Token"="APP"} `
  -ContentType "application/json" -Body '{"mode": "replay", "at": "2026-01-20T18:00", "speed": 0}'
#   ...ask for a plan, approve on the device, then:
Invoke-RestMethod URL/api/clock -Method Post -Headers @{"X-App-Token"="APP"} `
  -ContentType "application/json" -Body '{"speed": 900}'

# Back to real time
Invoke-RestMethod URL/api/clock -Method Post -Headers @{"X-App-Token"="APP"} `
  -ContentType "application/json" -Body '{"mode": "live"}'

# Devices and their telemetry; the agent's recent runs
Invoke-RestMethod URL/api/devices
Invoke-RestMethod URL/api/agent/runs
```

Interactive API docs: `URL/api/docs`. Logs: `modal app logs wind-buddy`.

## Good to know

- **State is in memory.** A redeploy restarts the container: proposals, approved plans and the
  run log start empty, and the clock goes back to its starting mode. The device just keeps
  polling and shows *Wind now* until a new plan is sent.
- **One container on purpose** (`max_containers=1`): all requests must see the same state.
  Plenty for a few devices and a demo.
- **Cost:** one small always-on CPU container, from the Modal credits.
- **Vercel** (`vercel.json`) can still host the read-only January views, but not the agent and
  devices, which need the shared state above.
