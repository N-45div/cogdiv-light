# Architecture

How Wind Buddy works, end to end: from a sentence spoken to the board, to a relay switching a load
in the windiest, cleanest hours. Every diagram matches the code; the file to read is named under
each one.

1. [The system](#1-the-system)
2. [Data pipelines](#2-data-pipelines)
3. [The agent: plan, check, rewrite](#3-the-agent-plan-check-rewrite)
4. [The fact checker](#4-the-fact-checker)
5. [A plan's life on the server](#5-a-plans-life-on-the-server)
6. [The board](#6-the-board)
7. [Voice, end to end](#7-voice-end-to-end)
8. [Compute jobs: which region, and when](#8-compute-jobs-which-region-and-when)
9. [Background loops: the watcher and the grid brief](#9-background-loops-the-watcher-and-the-grid-brief)
10. [Memory, deployment and failure](#10-memory-deployment-and-failure)
11. [Code map](#11-code-map)

## 1. The system

```mermaid
flowchart LR
    person((Person))
    subgraph board["Board: ESP32-S3"]
        mic[Mic] ~~~ spk[Speaker] ~~~ screen[Screen] ~~~ button[BOOT button] ~~~ relay[Relay]
    end
    browser["Browser: landing page and console"]

    subgraph modal["Modal: one always-on container"]
        api[FastAPI]
        agent["Agent loop + fact checker"]
        planners["Planners: wind, carbon, compute"]
        loops["Background: watcher, grid brief, snapshots"]
        piper["Piper text to speech"]
    end
    volume[("Modal Volume: saved state")]

    subgraph meta["Meta Model API"]
        spark["Muse Spark 1.3: planner"]
        whisper["Muse Voice Transcribe"]
    end
    baseten["Baseten: Muse Spark Open, open weights"]
    elexon["Elexon BMRS: wind actuals and forecasts"]
    neso["NESO Carbon Intensity API: 14 regions"]

    person -- speaks, presses --> board
    board -- "polls every 5 s, sends speech and presses" --> api
    browser --> api
    api --> agent --> planners
    agent --> spark
    agent -. fallback .-> baseten
    api --> whisper
    loops --> baseten
    planners --> elexon
    planners --> neso
    api --> piper
    loops <--> volume
```

- **The board is thin.** It shows what the server says, sends what it hears and presses, and
  switches the relay when told. It never decides anything.
- **The models propose, code decides.** Every number in a plan comes from a tested planner. The
  model only chooses which tools to call and explains the result, and its explanation is checked.
- **One process.** Boards, plans and the run log live in memory in one container, saved to a
  volume every 30 seconds.

## 2. Data pipelines

```mermaid
flowchart TB
    subgraph wind["Wind: Elexon BMRS, free, no key"]
        fuelhh["FUELHH: actual wind, half-hourly"]
        windfor["WINDFOR: forecast runs, hourly, 48 h ahead"]
    end
    subgraph carbon["Carbon: NESO, free, no key"]
        regional["Regional intensity fw48h: GB + 14 regions, half-hourly, with fuel mix"]
    end

    fuelhh & windfor --> window["LiveWindow: last 30 days, topped up every 15 min, keeps old data if a top-up fails"]
    window --> outlook["Outlook: newest forecast x its track record by lead time, 80% range from 28 days of errors"]
    regional --> feed["CarbonFeed: one download per half-hour, keeps the last one if a refresh fails"]

    outlook --> flex["Charge planner: windiest half-hours"]
    feed --> flex
    feed --> compute["Compute planner: cleanest region and half-hours"]
    feed --> brief["Grid brief facts"]
    flex & compute --> tools["Agent tools: small, pre-digested results"]
```

- **The planning clock.** Every planner works "as of" a moment from the clock. Live, that is now.
  In replay it moves through January 2026, and only forecasts published by then count, so there
  is no look-ahead.
- **Correcting the forecast.** In January the official day-ahead forecast ran high in 85% of
  hours. The expected value scales it by how metered wind compared with forecasts made as far
  ahead over the last 28 days, and the range comes from the spread of that ratio.
- **Carbon.** NESO forecasts grams of CO2 per kWh for GB and each distribution region. Wind makes
  it swing: on a windy night North Scotland is near 0 while the South West burns gas at 350 or
  more.

Code: `store.py` (LiveWindow), `outlook.py`, `elexon.py`, `carbon.py`.

## 3. The agent: plan, check, rewrite

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Asking: question + UK time + tools
    Asking --> RunningTools: model calls tools
    RunningTools --> Asking: results back as JSON
    Asking --> Checking: final answer
    Checking --> Verified: every figure matches
    Checking --> Rewriting: a figure does not match, first time
    Rewriting --> Asking: "these figures don't match, rewrite"
    Checking --> Unverified: still wrong after one rewrite
    Asking --> Failed: 6 model calls with no answer, or no model up
    Verified --> Proposed: the plan is feasible and a board is named
    Verified --> [*]: just an answer
    Unverified --> [*]: shown, never sent to a board
    Proposed --> [*]
    Failed --> [*]
```

The tools:

| Tool | Returns |
|---|---|
| `get_wind_outlook` | Hour-by-hour forecast, expected value and range, track record, windiest and calmest hour |
| `check_recent_wind` | How actual wind has tracked the forecast over the last few hours |
| `plan_charge` | Charging windows for a household load, wind while charging vs plugging in now, grid carbon; `goal: carbon` plans for the lowest carbon instead |
| `get_carbon_outlook` | GB carbon now and hour by hour, cleanest and dirtiest hours, every region ranked now |
| `plan_compute_job` | The region and half-hours with the least CO2 for a GPU job, against starting now in London |

Results are small and already worked out (UK times, whole numbers, every comparison as a
percentage), so the model never has to calculate. Models are tried in order: Muse Spark 1.3 on
the Meta Model API, then Muse Spark Open on Baseten. Every call records which model answered.

Code: `agent/runner.py`, `agent/tools.py`, `agent/llm.py`.

## 4. The fact checker

```mermaid
flowchart LR
    answer[Model's answer] --> extract["Find times and numbers; skip dates and years"]
    evidence["Tool results + the question"] --> known["Every number and time in them"]
    extract --> match{"Each figure matches a known one?"}
    known --> match
    match -- yes, all --> pass["Verified: may reach a board"]
    match -- no --> fail["List the figures that don't match"]
```

- Times ("02:00", "7 am") must appear in a tool result or the question.
- Numbers with a unit (MW, GW, kW, kWh, %, hours) must equal a known number, allowing honest
  rounding: "6,600 MW" or "6.6 GW" for 6,628, but not "7,200".
- Bare numbers up to 12 ("two windows") are not checked; larger ones are.
- Magnitudes are compared, not signs: "33% lower" matches a change of -33.3.

The same checker guards the watcher's notes and the grid brief. Code: `agent/factcheck.py`.

## 5. A plan's life on the server

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Proposed: verified, feasible plan sent to the board
    Proposed --> Proposed: a newer plan replaces it
    Proposed --> Idle: tap = no
    Proposed --> Active: hold = yes
    state Active {
        [*] --> Waiting
        Waiting --> Running: a run window starts, by the planning clock
        Running --> Waiting: the window ends, more to come
        Running --> Done: the last window ends
    }
    Active --> Idle: "stop" said to the board
    Active --> BetterOffered: watcher finds 5% more wind
    BetterOffered --> Active: hold = switch to the new plan
    BetterOffered --> Active: tap = keep the old plan
    Done --> Proposed: a new plan
```

- The relay is on exactly when the planning clock is inside one of the active plan's windows.
  In a fast replay the real relay follows simulated time.
- Each change bumps the board's state version, so the board knows to redraw.
- What the board says is built by code from the planner's numbers, in one or two sentences:
  "Here's my plan: charge from 2 am to 4 am, when the wind should be about 15 gigawatts, 50
  percent more than plugging in now. Hold the button for yes, or tap for no."

Code: `device.py` (registry, `screen`, `news`), `device_api.py`.

## 6. The board

```mermaid
stateDiagram-v2
    [*] --> Boot
    Boot --> Setup: no Wi-Fi saved
    Setup --> Boot: phone sets Wi-Fi in the "WindBuddy-Setup" portal
    Boot --> Polling: Wi-Fi up
    state Polling {
        [*] --> Idle
        Idle --> Proposal: server sends a plan
        Proposal --> Waiting: hold 1 s = approve
        Proposal --> Idle: tap = reject
        Waiting --> Running: relay on
        Running --> Waiting: relay off
        Running --> JobDone
        Waiting --> JobDone
        JobDone --> Idle: next plan
        Idle --> Listening: hold BOOT 0.35 s
        Waiting --> Listening: hold BOOT
        Running --> Listening: hold BOOT
        Listening --> Sending: release, or 8 s, or quiet after speech
        Sending --> Idle: server answers on the next polls
    }
    Polling --> Offline: 3 polls fail
    Offline --> Polling: a poll works
    Offline --> RelayOff: no server for 10 min
    RelayOff --> Polling: a poll works
    Polling --> Updating: server offers new firmware
    Updating --> Trial: written, checked by MD5, restart
    Trial --> Polling: reached the server in 3 min, kept
    Trial --> Rollback: crashed or never reached the server
    Rollback --> Polling: previous firmware, won't retry that image
```

- **One button.** Tap is no (or refresh), a 1 s hold is yes, and a hold in any other screen is
  push-to-talk. Holding 10 s resets Wi-Fi.
- **Fails safe.** No server for 10 minutes switches the load off.
- **Looked after remotely.** The board sends its last 30 log lines with each event, and installs
  firmware the server offers, with automatic rollback.

Code: `firmware/src/main.cpp`, `firmware/src/audio.h`, `firmware.py`.

## 7. Voice, end to end

```mermaid
sequenceDiagram
    actor P as Person
    participant B as Board
    participant S as Server
    participant V as Muse Voice Transcribe
    participant M as Muse Spark 1.3
    participant T as Piper
    P->>B: holds BOOT, "64 GPUs for 6 hours, where and when?"
    B->>S: POST /api/device/{id}/voice (16 kHz WAV)
    S->>V: transcribe
    V-->>S: text
    alt "stop charging"
        S->>S: stop the active plan
    else a question
        S->>M: agent loop with tools
        M-->>S: answer
        S->>S: fact check; propose a verified, feasible plan
    end
    B->>S: next poll
    S->>T: the plan in one or two sentences
    T-->>S: speech
    S-->>B: state, with the new clip's id
    B->>S: GET speech clip as ADPCM
    S-->>B: about 8 KB per second, played as it downloads
    B-->>P: "Here's my plan... hold the button for yes"
    P->>B: holds the button
    B->>S: approve
```

- Transcribing and planning take about 25 s of network calls. They run in a worker thread, so
  the server keeps answering polls meanwhile.
- Meta's API has no text to speech yet, so the voice is Piper (open source) on the server.

Code: `device_api.py` (`device_voice`, `announce`), `voice.py`.

## 8. Compute jobs: which region, and when

```mermaid
flowchart TB
    job["Job: GPUs x hours, deadline, can it pause?"] --> energy["Energy: 1.3 kW per GPU x hours"]
    energy --> each["For each of the 14 regions"]
    each --> slots["Half-hours from now to the deadline, with that region's g/kWh"]
    slots --> fill{"Can it pause?"}
    fill -- yes --> cleanest["The cleanest half-hours, wherever they are"]
    fill -- no --> run["The cleanest unbroken run"]
    cleanest & run --> kg["kg CO2 = energy x g/kWh"]
    kg --> best["Least CO2 wins"]
    best --> compare["Compare with London: straight away, at its cleanest time, and the best region straight away"]
```

- A data centre can't follow the wind; the jobs inside it can. A checkpointed training run only
  has to finish by its deadline.
- The comparison splits the saving into moving in time and moving in place.
- The same allocator schedules EV charges, so both are covered by the same tests.

Code: `compute.py`, `flex.py` (`allocate`), `carbon_api.py` (`POST /api/compute/plan`).

## 9. Background loops: the watcher and the grid brief

```mermaid
stateDiagram-v2
    direction LR
    state "Watcher, every 30 min of the planning clock" as W {
        [*] --> Replan: remaining energy, newest forecast
        Replan --> Better: 5% or more wind
        Replan --> Below: remaining windows 15% or more less windy
        Replan --> OnTrack
        Better --> [*]: propose "Better window?"
        Below --> [*]: alert line on the board
        OnTrack --> [*]: clear any alert
    }
    state "Grid brief, every half-hour" as G {
        [*] --> Facts: code works out the facts
        Facts --> Draft: Muse Spark Open writes them up
        Draft --> Publish: figures check out
        Draft --> Redraft: a figure is wrong, once
        Redraft --> Publish: fixed
        Redraft --> CodeText: still wrong, or no model
        CodeText --> Publish
        Publish --> [*]
    }
```

- The watcher re-plans wind-led charges. Its note is written by Muse Spark Open and
  fact-checked, with a sentence written by code as the fallback.
- The brief gives the open-weight model a job every half-hour. `GET /api/brief` never waits on a
  model: it returns the newest brief.

Code: `watcher.py`, `watch_api.py`, `brief.py`.

## 10. Memory, deployment and failure

```mermaid
flowchart LR
    subgraph container["Modal container, min 1, max 1"]
        mem["Memory: boards, plans, run log, watcher findings"]
        snap["Every 30 s if something changed, and at shutdown"]
    end
    mem --> snap --> vol[("Modal Volume: server.pkl")]
    vol -- "read at start-up" --> mem
    push["git push to main"] --> ci["CI: ruff, pytest, oxlint, tsc, vitest, build, Docker smoke test"]
    ci --> deploy["modal deploy: image with the API, the built site, firmware, the Piper voice"]
    deploy --> container
```

| What fails | What happens |
|---|---|
| Elexon top-up fails | The data already held keeps being served; retry in 2 minutes |
| NESO download fails | The last carbon forecast is used while it still reaches 42 hours ahead |
| Meta API fails | The next model in the list (Muse Spark Open on Baseten) answers |
| Every model fails | The run is recorded with its error; nothing reaches a board |
| A figure doesn't match | One rewrite, then the answer is marked unverified and never sent |
| The server restarts | Boards, plans and the run log come back from the volume |
| The board loses the server | "No connection" on screen; the relay goes off after 10 minutes |
| New firmware is broken | The board rolls back to the previous image by itself |
| Text to speech fails | The board's poll still works; it just doesn't speak |

Guards: `APP_TOKEN` protects the endpoints that cost money or change shared state, and
`DEVICE_TOKENS` gives each board its own token.

## 11. Code map

| Area | Files |
|---|---|
| App and routes | `backend/wind_monitor/main.py`, `api.py`, `planning_api.py`, `agent_api.py`, `device_api.py`, `watch_api.py`, `carbon_api.py` |
| Data | `elexon.py`, `store.py`, `outlook.py`, `analysis.py`, `carbon.py` |
| Planning | `flex.py` (charges), `compute.py` (compute jobs) |
| Agent | `agent/runner.py`, `agent/tools.py`, `agent/llm.py`, `agent/factcheck.py`, `agent/runs.py` |
| Board | `device.py`, `voice.py`, `firmware.py`, `firmware/src/` |
| Background | `watcher.py`, `brief.py`, `persist.py`, `clock.py` |
| Website | `frontend/src/landing/` (landing page), `frontend/src/views/` (console tabs), `frontend/src/lib/` (helpers and tests) |
| Deploy | `deploy/modal_app.py`, `.github/workflows/ci.yml`, `.github/workflows/deploy.yml` |
