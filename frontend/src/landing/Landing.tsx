import type { ReactNode } from 'react'
import { api } from '../api'
import { latestDevice } from '../lib/conversation'
import { formatG } from '../lib/carbon'
import { modelName } from '../lib/agent'
import { formatUkTime } from '../lib/format'
import { useLoad, useNow, usePolling } from '../lib/hooks'
import { BoardArt } from './BoardArt'
import { SNAPSHOT, seriesFrom } from '../lib/wind'
import { WindChart } from './WindChart'

const APP = '/app/'
const REPO = 'https://github.com/N-45div/cogdiv-light'
const POLL_MS = 30_000

export function Landing() {
  return (
    <div className="landing">
      <Nav />
      <Hero />
      <Problem />
      <HowItWorks />
      <RealRun />
      <Board />
      <Models />
      <Results />
      <Compute />
      <Trust />
      <Closing />
      <Footer />
    </div>
  )
}

function Nav() {
  return (
    <header className="nav">
      <div className="nav-inner">
        <a className="logo" href="/">
          <img src="/favicon.svg" alt="" />
          Wind Buddy
        </a>
        <nav className="nav-links" aria-label="Sections">
          <a href="#how">How it works</a>
          <a href="#run">A real run</a>
          <a href="#board">The board</a>
          <a href="#results">Results</a>
          <a href="#compute">AI compute</a>
        </nav>
        <div className="nav-end">
          <a className="nav-quiet" href={REPO}>
            GitHub
          </a>
          <a className="btn primary" href={APP}>
            Open the console
          </a>
        </div>
      </div>
    </header>
  )
}

function Hero() {
  const outlook = useLoad(() => api.outlook(24), 'outlook')
  const live = outlook.data ? seriesFrom(outlook.data) : null
  return (
    <section className="hero" id="top">
      <div className="wrap hero-grid">
        <div className="hero-copy">
          <p className="label">A voice agent for your EV charger, on real hardware</p>
          <h1>Charge when Britain's wind blows.</h1>
          <p className="lede">
            Hold the button on a small ESP32 board and say what your car needs by when. An agent on Meta's
            Muse Spark plans the charge into the windiest hours, checks every number against live grid data,
            reads the plan back, and switches the charger only after you say yes.
          </p>
          <div className="cta-row">
            <a className="btn primary large" href={APP}>
              Open the live console <Arrow />
            </a>
            <a className="btn secondary large" href="#run">
              See a real run
            </a>
          </div>
          <p className="stack-row">
            <span>Muse Spark 1.3</span>
            <span>Muse Voice Transcribe</span>
            <span>Muse Spark Open</span>
            <span>Elexon BMRS live data</span>
          </p>
        </div>
        <div className="hero-board">
          <div className="board-frame">
            <BoardArt relayOn />
          </div>
          <figure className="said">
            <figcaption>
              <SpeakerIcon /> The board, 3 Nov 2026, 10:09
            </figcaption>
            <blockquote>
              “Here's my plan. Charge from 8 pm to 9:30 pm to get 10 kilowatt hours by 6 am. Shall I go ahead?
              Hold the button for yes, or tap for no.”
            </blockquote>
          </figure>
          <BoardStatus />
        </div>
      </div>
      <div className="wrap hero-chart">
        {/* While the live outlook loads (or if it can't), show this afternoon's snapshot. */}
        <WindChart series={live ?? SNAPSHOT} live={live !== null} />
      </div>
    </section>
  )
}

/** One line on whether the real board is checking in right now. */
function BoardStatus() {
  const devices = usePolling(api.devices, 'devices', POLL_MS)
  const now = useNow(POLL_MS)
  const device = latestDevice(devices.data)
  if (!device?.last_seen) return null
  const minutes = Math.round((now - Date.parse(device.last_seen)) / 60_000)
  const online = minutes < 2
  const ago = minutes < 60 ? `${minutes} min ago` : minutes < 48 * 60 ? `${Math.round(minutes / 60)} h ago` : 'days ago'
  return (
    <p className="board-status">
      <span className={online ? 'dot' : 'dot off'} />
      {online ? `${device.device_id} is online now` : `${device.device_id} last checked in ${ago}`}
    </p>
  )
}

function Problem() {
  return (
    <section className="section">
      <div className="wrap">
        <div className="split">
          <div>
            <p className="label">Why not a timer?</p>
            <h2>The wind doesn't keep office hours, and the forecast is optimistic.</h2>
          </div>
          <p className="section-lede">
            Britain's wind output swings by gigawatts from one night to the next. A midnight timer is a guess.
            Wind Buddy plans with the official forecast, corrected for how often it has over-promised.
          </p>
        </div>
        <div className="figures">
          <Figure value="85%" text="of hours in January 2026, the day-ahead wind forecast ran high" />
          <Figure value="2.0 GW" text="average miss at auction time, 18% of all wind output" />
          <Figure value="−31%" text="less error once the forecast is corrected for its track record" accent />
        </div>
      </div>
    </section>
  )
}

function Figure(props: { value: string; text: string; accent?: boolean }) {
  return (
    <div className="figure">
      <div className={props.accent ? 'figure-value accent' : 'figure-value'}>{props.value}</div>
      <p>{props.text}</p>
    </div>
  )
}

const STEPS = [
  {
    tag: 'Listen',
    title: 'Hold the button and talk',
    text: '“Ten kilowatt hours by six AM.” The board records it and Meta\'s Muse Voice Transcribe turns it into text.',
  },
  {
    tag: 'Plan',
    title: 'Muse Spark plans on live data',
    text: "It calls tools over the live Elexon wind outlook, corrected by the forecast's track record, and picks the windiest half-hours before your deadline.",
  },
  {
    tag: 'Check',
    title: 'Every number is checked',
    text: "Each time and figure in the answer must match a tool result. If one doesn't, the agent rewrites once, or the plan never reaches the board.",
  },
  {
    tag: 'Act',
    title: 'You say yes, it switches',
    text: 'The board reads the plan aloud. Hold for yes, tap for no. The relay switches at the planned half-hour; a watcher re-checks every 30 minutes.',
  },
]

function HowItWorks() {
  return (
    <Section id="how" label="How it works" title="From a sentence to a switched socket.">
      <ol className="cards four">
        {STEPS.map((s, i) => (
          <li key={s.tag} className="card">
            <div className="step-tag">
              {String(i + 1).padStart(2, '0')} {s.tag}
            </div>
            <h3>{s.title}</h3>
            <p>{s.text}</p>
          </li>
        ))}
      </ol>
    </Section>
  )
}

// The server's log of the first full voice run on the real board (3 Nov 2026, UK time).
const RUN: { at: string; who?: string; text: string; step?: boolean }[] = [
  { at: '10:09:27', who: 'You, holding the button', text: '“I need ten kilowatt hours by six AM. When should I charge?”' },
  { at: '10:09:27', text: 'Muse Voice Transcribe → Muse Spark plan → fact check passed → sent to desk-1', step: true },
  {
    at: '10:09:43',
    who: 'Wind Buddy, out loud',
    text: '“Here\'s my plan. Charge from 8 pm to 9:30 pm to get 10 kilowatt hours by 6 am. Shall I go ahead? Hold the button for yes, or tap for no.”',
  },
  { at: '10:10:27', text: 'Button held: plan approved', step: true },
  { at: '10:10:27', who: 'Wind Buddy, out loud', text: '“Great, it\'s booked. I\'ll start charging at 8 pm.”' },
  { at: '20:00', text: 'Booked: relay on until 21:30', step: true },
]

function RealRun() {
  return (
    <section className="section" id="run">
      <div className="wrap split top">
        <div className="run-intro">
          <p className="label">A real run</p>
          <h2>3 November, 10:09, on the board on my desk.</h2>
          <p className="section-lede">
            This is the server's log of the first full voice run, word for word. No script, no staging: one
            button, one sentence, one yes.
          </p>
        </div>
        <ol className="run-log">
          {RUN.map((r, i) => (
            <li key={i} className={r.step ? 'step' : r.who?.startsWith('Wind') ? 'board' : 'you'}>
              <time>{r.at}</time>
              <div>
                {r.who && <div className="run-who">{r.who}</div>}
                <div className="run-text">{r.text}</div>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </section>
  )
}

const SPECS: [string, string][] = [
  ['Board', 'ESP32-S3 (OceanLabz AI Explorer V3.2)'],
  ['Listens', 'I2S microphone, 16 kHz, push to talk'],
  ['Speaks', 'British voice streamed as 16 kHz ADPCM, 8 KB a second: it starts talking before the download ends'],
  ['Switches', 'Relay output for a charger, heater or anything that can wait for the wind'],
  ['Updates', 'Over the air, with automatic rollback; its log is readable from the console'],
  ['Fails safe', "If it can't reach the server for 10 minutes, the load goes off"],
]

function Board() {
  return (
    <Section id="board" label="The board" title="One button, a mic, a speaker and a relay.">
      <div className="split top">
        <p className="section-lede">
          No app and no screen to read. The board is the interface: you talk to it, it talks back, and the only
          thing you press is the button that says yes. Everything clever happens on the server, so the board
          can't be talked into anything the fact checker hasn't seen.
        </p>
        <dl className="specs">
          {SPECS.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </div>
    </Section>
  )
}

function Models() {
  return (
    <Section id="models" label="Under the hood" title="Three Meta models and one strict referee.">
      <div className="cards four">
        <Model role="Ears" name="Muse Voice Transcribe">
          Push-to-talk speech to text, hinted with energy words like “kilowatt hours”.
        </Model>
        <Model role="Planner" name="Muse Spark 1.3">
          Calls tools over live grid data and explains the plan. 27 of 27 test nights verified, median 4 seconds.
        </Model>
        <Model role="Backup and watcher" name="Muse Spark Open">
          Open weights on Baseten. Takes over if the main API fails, and writes the watcher's notes and the
          live grid brief.
        </Model>
        <Model role="Referee, plain code" name="Fact checker" dark>
          Every time and number must trace back to a tool result. Models propose; code verifies.
        </Model>
      </div>
      <p className="footnote">
        Meta's API has no text-to-speech yet, so the voice is Piper: open source, running on our server.
      </p>
    </Section>
  )
}

function Model(props: { role: string; name: string; children: ReactNode; dark?: boolean }) {
  return (
    <div className={props.dark ? 'card dark' : 'card'}>
      <div className="model-role">{props.role}</div>
      <h3>{props.name}</h3>
      <p>{props.children}</p>
    </div>
  )
}

// Backtest, October 2026 on live data, 25 nights (docs/DECISIONS.md C9): wind output while
// charging, as a gain over charging the moment the car is plugged in.
const BARS = [
  { label: 'Charge at plug-in', gain: 0 },
  { label: 'Midnight timer', gain: 1.9 },
  { label: 'Wind Buddy', gain: 12.1, us: true },
  { label: 'Perfect hindsight', gain: 13.8, ghost: true },
]

function Results() {
  const max = Math.max(...BARS.map((b) => b.gain))
  return (
    <Section id="results" label="Results" title="More wind in every charge. We measured.">
      <div className="results">
        <div className="card bars-card">
          <h3>Wind captured while charging, vs charging at plug-in</h3>
          <p className="muted">October 2026, live data, 25 nights</p>
          <ul className="bars">
            {BARS.map((b) => (
              <li key={b.label} className={b.us ? 'us' : b.ghost ? 'ghost' : ''}>
                <span className="bar-label">{b.label}</span>
                <span className="bar-track">
                  {b.gain > 0 && <span className="bar" style={{ width: `${(b.gain / max) * 100}%` }} />}
                </span>
                <span className="bar-value">{b.gain === 0 ? 'baseline' : `+${b.gain}%`}</span>
              </li>
            ))}
          </ul>
          <p className="footnote">
            Each night a car plugs in at 18:00 needing 21 kWh at 7 kW by 07:00. Score: GB wind output while it
            charged. Method in <a href={`${REPO}/blob/main/docs/DECISIONS.md`}>docs/DECISIONS.md</a>, C9.
          </p>
        </div>
        <div className="result-figures">
          <Stat value="88%" text="of the best possible gain, on live October data" />
          <Stat value="+10%" text="more wind than a midnight timer (+14% in January)" />
          <Stat value="27/27" text="plans passed the fact check, on both Muse Spark models" />
        </div>
      </div>
    </Section>
  )
}

function Stat(props: { value: string; text: string }) {
  return (
    <div className="card stat">
      <div className="stat-value">{props.value}</div>
      <p>{props.text}</p>
    </div>
  )
}

const TRUST: [string, string][] = [
  ['Nothing runs without your yes', 'Plans wait on the board for a button press. Tap to say no.'],
  ['Numbers with receipts', 'Every figure traces to a tool result, and the console shows the trace.'],
  ['Honest about uncertainty', 'An 80% likely range, built from how the forecast has really performed.'],
  ['Fails safe', 'No server for 10 minutes means the load goes off.'],
]

function Compute() {
  const carbon = usePolling(() => api.carbon(1), 'carbon-now', POLL_MS * 10)
  const brief = usePolling(api.brief, 'brief', POLL_MS * 10)
  const regions = carbon.data?.regions ?? []
  const max = Math.max(...regions.map((r) => r.now_g ?? 0), 1)
  const b = brief.data
  return (
    <Section id="compute" label="Beyond the driveway" title="The same idea, for AI's power bill.">
      <div className="split top">
        <p className="section-lede">
          A data centre can't follow the wind. The jobs inside it can. A checkpointed training run or an overnight
          batch only has to finish by its deadline, so Wind Buddy picks the region and the half-hours where Britain's
          grid is cleanest, from NESO's forecast for 14 regions, and compares it with starting now in London.
        </p>
        <div className="compute-actions">
          <a className="btn primary" href={`${APP}?view=grid`}>
            Plan a compute job <Arrow />
          </a>
          <p className="muted">Or ask the board: “I need 32 GPU-hours before 7 am. Where and when?”</p>
        </div>
      </div>
      <div className="compute-live">
        <div className="card brief-card">
          <p className="model-role">Live grid brief</p>
          <p className="brief-quote">{b ? b.text : 'Reading the grid…'}</p>
          {b && (
            <p className="muted brief-by">
              {b.by === 'code'
                ? `Written by code at ${formatUkTime(b.at)} UK time.`
                : `Written by ${modelName(b.by)} at ${formatUkTime(b.at)} UK time; ${b.check}.`}
            </p>
          )}
        </div>
        <div className="card regions-card">
          <h3>Grams of CO2 per kWh, right now</h3>
          {regions.length === 0 && <p className="muted">Loading NESO's forecast…</p>}
          <ul className="region-bars">
            {regions.map((r) => (
              <li key={r.id}>
                <span className="region-name">{r.name}</span>
                <span className="bar-track">
                  <span className="bar" style={{ width: `${((r.now_g ?? 0) / max) * 100}%` }} />
                </span>
                <span className="region-value">{r.now_g === null ? 'n/a' : Math.round(r.now_g)}</span>
              </li>
            ))}
          </ul>
          {carbon.data && (
            <p className="footnote">
              NESO Carbon Intensity API, forecast for {formatUkTime(carbon.data.times[0])} UK time. Great Britain as a
              whole: {formatG(carbon.data.gb_now_g)}.
            </p>
          )}
        </div>
      </div>
    </Section>
  )
}

function Trust() {
  return (
    <Section id="trust" label="Trust" title="It never acts alone.">
      <div className="trust">
        {TRUST.map(([title, text]) => (
          <div key={title}>
            <h3>{title}</h3>
            <p>{text}</p>
          </div>
        ))}
      </div>
    </Section>
  )
}

function Closing() {
  return (
    <section className="section">
      <div className="wrap">
        <div className="closing">
          <div>
            <p className="closing-label">One board is a home. A thousand is a power station.</p>
            <h2>It's running right now.</h2>
            <p className="closing-text">
              The server, the agent and the board are live. Open the console to watch the wind, the plans and the
              board's conversations as they happen.
            </p>
          </div>
          <div className="cta-row">
            <a className="btn white large" href={APP}>
              Open the live console
            </a>
            <a className="btn outline-white large" href={REPO}>
              Read the code
            </a>
          </div>
        </div>
      </div>
    </section>
  )
}

function Footer() {
  return (
    <footer className="wrap site-footer">
      <span className="footer-name">Wind Buddy</span>
      <span className="footer-text">
        Built for the Meta Global AI Developer Hackathon 2026. Grid data: Elexon BMRS (FUELHH, WINDFOR) and
        the NESO Carbon Intensity API.
      </span>
      <nav aria-label="Links">
        <a href={APP}>Console</a>
        <a href="/api/docs">API</a>
        <a href={REPO}>GitHub</a>
      </nav>
    </footer>
  )
}

function Section(props: { id: string; label: string; title: string; children: ReactNode }) {
  return (
    <section id={props.id} className="section">
      <div className="wrap">
        <p className="label">{props.label}</p>
        <h2 className="section-title">{props.title}</h2>
        <div className="section-body">{props.children}</div>
      </div>
    </section>
  )
}

function Arrow() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  )
}

function SpeakerIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M11 5 6 9H3v6h3l5 4V5z" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7" />
      <path d="M18.5 5.5a9 9 0 0 1 0 13" />
    </svg>
  )
}
