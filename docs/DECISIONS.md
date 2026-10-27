# Design decisions

Every significant choice in this project, with the options considered, what was chosen and
why, and the trade-off accepted. Read top to bottom for a walkthrough of the design.

---

## Part A: product and analysis decisions

### A1. What "purchase decisions in January 2026" covers

- **Context:** Day-ahead power is bought on day D-1 for delivery on day D.
- **Decision:** decision days are 1-31 January 2026, so **delivery days are 2 January to 1 February 2026**.
- **Why:** the brief scopes by *decision* date, not delivery date. 1 Feb is included because it is bought on 31 Jan.
- **Code:** `backend/wind_monitor/config.py` (`DECISION_FIRST`, `DELIVERY_FIRST`, ...).

### A2. When is the decision made? (the cut-off)

- **Options:** a fixed time, a user input, or the actual market deadlines.
- **Decision:** default **09:20 UK time on D-1**, the gate closure of the EPEX SPOT GB day-ahead auction. 09:50 (Nord Pool N2EX GB day-ahead auction) is a preset, and any custom time can be typed in.
- **Why:** the desk can only use forecasts published *before* it commits. Tying the cut-off to the real auction deadline makes the analysis match their workflow. Making it adjustable lets each desk set its own internal deadline (for example, an hour before gate closure).
- **Sources:** [Nord Pool GB auction](https://support.nordpoolgroup.com/support/solutions/articles/8000088463-about-the-day-ahead-gb-auction), [S&P Platts notice](https://www.spglobal.com/commodity-insights/en/pricing-benchmarks/our-methodology/subscriber-notes/120920-platts-to-amend-uk-day-ahead-power-assessments-timing-from-jan-4).

### A3. Which forecast did the desk have at the cut-off?

- **Context:** WINDFOR is published several times a day. Each publication (a "run", identified by `publishTime`) forecasts the next ~2 days.
- **Decision:** for each hour of the delivery day, use the **latest forecast published at or before the cut-off** that covers that hour.
- **Why:** this is exactly what was knowable at decision time, with no look-ahead. Choosing per hour (rather than "the latest run, full stop") also copes with a run that only partly covers the day: older runs fill the gaps.
- **Code:** `analysis.decision_forecast`. Tests in `tests/test_analysis.py` cover "after cut-off is ignored", "exactly at cut-off counts" and "older run fills gaps".

### A4. What is a "delivery day"?

- **Decision:** UK local midnight to midnight (the GB settlement day). It is computed with real time zones, so clock-change days are 23 or 25 hours long.
- **Why:** this matches how Elexon's settlement data is organised. January is GMT (= UTC), but the code stays correct if the scope ever moves into summer (tested in `test_clock_change_day_is_23_hours_long`).

### A5. Comparing hourly forecasts with half-hourly actuals

- **Options:** (a) compare only on the hour, (b) average each pair of half-hours, (c) interpolate the forecast to half-hours.
- **Decision:** **(a) compare only where both exist (on the hour)**. The chart still shows every half-hourly actual.
- **Why:** it is the simplest choice and invents no values. Option (b) assumes the forecast is an hourly average, and (c) assumes it varies linearly; neither is documented.
- **Trade-off:** half the actual points are not scored. With 24 points a day for 31 days (~744 pairs) there is plenty of data.

### A6. Sign of the error

- **Decision:** `error = forecast - actual`. **Positive means the forecast was too high**: less wind came than expected, so the desk was **short** and had to buy the gap later (intraday or imbalance), usually at a worse price.
- **Why:** it reads naturally for a buyer: positive = "I need more power". The UI colours short red and long blue.

### A7. Which accuracy metrics

| Metric | Why it is shown |
|---|---|
| **MAE** (mean absolute error, MW) | The headline: "on average we were off by X MW". Easy to explain. |
| **nMAE** (MAE as % of mean wind output) | Scale-free, so months or regions can be compared. |
| **P90 absolute error** | Risk view: "9 hours in 10, the error was within X MW", which is the margin to hold. |
| **Bias** (mean error) | Direction: does the forecast systematically leave us short or long? |
| **RMSE** | Penalises big misses; a large RMSE/MAE gap means occasional large errors. |
| **Largest miss**, **histogram** | The tail risk and the shape of the error distribution. |

- **Rejected: MAPE** (mean absolute percentage error). Wind can drop close to zero, and dividing by near-zero actuals makes MAPE explode and mislead.
- **Code:** `analysis.error_summary`, `error_by_day`, `error_by_hour`, `error_histogram`.

### A8. The 80% range around the forecast

- **Decision:** the band is the **10th to 90th percentile of past decision-forecast errors at the same hour of day**, taken from the *other* delivery days (leave-one-day-out), then subtracted from the forecast. It is clipped at 0 MW, because wind output can't be negative.
- **Why:** it is empirical, so it makes no assumption about error shape. Grouping by hour of day captures that later hours are forecast further ahead and are less certain. Excluding the day itself stops the band being built from the outcome it is meant to bracket.
- **Trade-off / honesty note:** it still uses other January days, including later ones, so it is an evaluation tool, not a live forecast. A production version would use a rolling window of *past* days only. It also rests on ~30 samples per hour, so the percentiles are approximate.
- **"Low-wind case" tile:** the sum of the hourly low cases. It is deliberately cautious, since not every hour will hit its low case on the same day.

### A9. Accuracy vs lead time

- **Decision:** score **every** forecast run against actuals, bucketed by how many hours before delivery it was made (0-48 h), and shade the lead times used for day-ahead decisions.
- **Why:** this shows the cost of committing a day early, and the size of the opportunity for intraday re-trading.

### A10. Data definitions and caveats

- Actuals: Elexon **FUELHH** with `fuelType=WIND`, using `startTime` and `generation` (MW). If an actual is re-published, the latest publication is kept.
- Forecasts: Elexon **WINDFOR**, using `publishTime`, `startTime` and `generation` (MW).
- Both cover **transmission-metered wind**. Wind embedded in distribution networks is not in FUELHH (it shows up as lower demand), so national wind output is higher than shown here. This matches the brief ("Elexon data alone").

---

## Part B: engineering decisions

### B1. Repository layout

`backend/` (Python package + tests), `frontend/` (React app), `api/index.py` (a 5-line Vercel adapter), plus Docker and CI at the root. One repo keeps the API contract and its consumer in the same commit history.

### B2. Backend: FastAPI + pandas

- **FastAPI:** typed request validation, Pydantic response models (`schemas.py`) that double as documentation at `/api/docs`, and a test client for end-to-end tests. Handlers are plain `def`, so FastAPI runs the pandas work in a thread pool.
- **pandas:** the analysis is time-series joins, group-bys and quantiles, which pandas does in a few readable lines.
- **Layering:** `elexon.py` (HTTP + parsing) → `store.py` (where data comes from) → `analysis.py` (pure functions, no I/O) → `api.py` (thin routes). Each layer is testable on its own; the business rules live in `analysis.py` with no web or network code.

### B3. Data strategy: committed snapshot with live fallback

- **Options:** (a) call Elexon on every request, (b) a database refreshed on a schedule, (c) a snapshot file committed to the repo.
- **Decision:** **(c)**, with live fetching as a fallback (`DATA_SOURCE=auto|snapshot|live`).
- **Why:** January 2026 is in the past, so the data no longer changes. A snapshot means fast serverless cold starts, no Elexon dependency during a client demo, reproducible numbers, and no database to run. The `Refresh data snapshot` GitHub Actions workflow re-downloads it on demand.
- **Trade-off:** a "live" product covering future months would move to (b), with a scheduled job and a small database. The code is ready for that: only `store.py` changes.
- **Resilience:** the Elexon client splits requests into 7-day chunks, retries 429/5xx with exponential backoff, and fails fast on other errors.

### B4. Caching

- In-process: per-dataset cache of computed results (for example, errors per cut-off), so repeat requests are instant.
- HTTP: data endpoints send `Cache-Control: s-maxage=86400`, so Vercel's CDN answers repeat requests without running Python.

### B5. One origin everywhere (no CORS)

The browser always calls `/api/...` on the same host: through Vite's proxy in development, from the single container in Docker, and through a rewrite on Vercel. There is no CORS configuration to get wrong.

### B6. Frontend: React + TypeScript + Vite + Recharts

- **TypeScript** types in `api.ts` mirror the backend schemas, so a contract change breaks the build, not the user.
- **State:** the view, delivery day and cut-off live in the **URL**, so every screen can be bookmarked or shared (useful for a trading desk), with no state library. Data loading is a ~30-line hook (`useLoad`) that keeps the previous data on screen while new data loads. TanStack Query is the natural upgrade if the app grows.
- **Recharts:** declarative React charts; enough for line, area, bar and range charts at these data sizes.
- **Formatting** in UK time via `Intl`, assembled from parts so the output is the same in every browser.

### B7. Charts and accessibility

- Colours come from a palette checked with a validator for colour-blind separation and contrast, in **both light and dark mode**.
- Red/blue is used for short/long; the forecast is always blue and the actual always orange.
- **Every chart has a "Show table" view**, and tooltips are never the only way to read a value.
- Mobile layout is checked at 390 px wide with no horizontal scroll.

### B8. Testing strategy

| Layer | How | Where |
|---|---|---|
| Business rules | Tiny hand-made tables with obvious answers (cut-off selection, clock changes, error sign, band, lead-time buckets) | `backend/tests/test_analysis.py` |
| Elexon client | HTTP mocked with `respx`: chunking, parameters, retries, parsing | `backend/tests/test_elexon.py` |
| Snapshot store | Round-trip to disk, source selection | `backend/tests/test_store.py` |
| API end to end | FastAPI test client on a known dataset, including input validation | `backend/tests/test_api.py` |
| Frontend logic | Vitest for formatting (incl. BST) and insight calculations | `frontend/src/lib/*.test.ts` |
| Whole system | CI builds the Docker image, starts it and calls the site and API | `.github/workflows/ci.yml` |

### B9. Docker

A multi-stage build: Node builds the React app, then a `python:3.12-slim` image serves the API and static files. Dependency layers are copied first for fast rebuilds, the app runs as a **non-root** user, and there is a **health check**. `docker compose up` runs it like production; `docker-compose.dev.yml` gives hot reload on both sides.

### B10. Deployment: Vercel

- **Options:** Vercel (free, always on, CDN) or Heroku (deploys the Docker image as-is, but paid, and the cheapest plan sleeps).
- **Decision:** **Vercel**: the React build is served from the CDN, and FastAPI runs as one Python serverless function (`api/index.py`). Docker is used for local runs and CI.
- **Trade-off:** Vercel doesn't run our Docker image, so CI builds and smoke-tests the image separately to keep it healthy.

### B11. Reproducibility and hygiene

- Python 3.12 everywhere (`.python-version`, Docker, CI, Vercel). Dependencies are pinned, and `package-lock.json` is committed.
- Linting and formatting: `ruff` (Python), `oxlint` and `tsc` (TypeScript), all enforced in CI.
- Small, single-purpose commits whose messages explain *why*.
- Configuration through environment variables (`DATA_SOURCE`, `SNAPSHOT_DIR`, `STATIC_DIR`, `ELEXON_BASE_URL`); no secrets are needed.

---

## What I would do next

1. Build the uncertainty band from a rolling window of *past* days only, and check its calibration (does the 80% range contain ~80% of outcomes?).
2. Add national demand forecasts (Elexon NDF) to turn "wind forecast" into "power still to buy = demand - wind", with ranges.
3. Normalise errors by installed wind capacity, and extend beyond January with a scheduled refresh into a small database.
4. Alerts when the latest run moves far from the run used for the decision.
5. Browser end-to-end tests (Playwright) for the main flows.
