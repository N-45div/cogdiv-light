/** A drawing of the Wind Buddy board: speaker, mic, BOOT button, ESP32-S3 and the relay. */
export function BoardArt(props: { relayOn?: boolean; className?: string }) {
  const on = props.relayOn
  return (
    <svg
      className={props.className}
      viewBox="0 0 360 220"
      role="img"
      aria-label={`The Wind Buddy board: speaker, microphone, BOOT button, ESP32-S3 and a relay${
        on ? ', switched on' : ''
      }`}
    >
      <defs>
        <pattern id="grille" width="7" height="7" patternUnits="userSpaceOnUse" x="-3.5" y="-3.5">
          <circle cx="3.5" cy="3.5" r="1.8" fill="#3b5488" />
        </pattern>
      </defs>
      <rect x="20" y="24" width="320" height="172" rx="16" fill="#0f2147" stroke="#24407a" />
      <g fill="#0a1733" stroke="#2f4c86">
        <circle cx="44" cy="44" r="5" />
        <circle cx="316" cy="44" r="5" />
        <circle cx="44" cy="176" r="5" />
        <circle cx="316" cy="176" r="5" />
      </g>
      <g stroke="#26467f" strokeWidth="1.4" fill="none">
        <path d="M118 110 H150 V86 H168" />
        <path d="M118 124 H156 V150 H168" />
        <path d="M252 96 H272 V76 H292" />
        <path d="M252 138 H280 V160" />
      </g>
      {/* speaker and mic */}
      <circle cx="80" cy="104" r="34" fill="#0a1733" stroke="#2f4c86" strokeWidth="2" />
      <circle cx="80" cy="104" r="26" fill="url(#grille)" />
      <circle cx="80" cy="160" r="4.5" fill="#0a1733" stroke="#5b8cff" strokeWidth="1.6" />
      {/* ESP32-S3 module */}
      <rect x="168" y="64" width="84" height="96" rx="6" fill="#0a1733" stroke="#2f4c86" />
      <rect x="176" y="72" width="68" height="58" rx="3" fill="#c9d3e3" />
      <text x="210" y="98" textAnchor="middle" className="art-chip">
        ESP32-S3
      </text>
      <text x="210" y="112" textAnchor="middle" className="art-chip small">
        Wi-Fi · 240 MHz
      </text>
      <path d="M182 140 h8 v8 h8 v-8 h8 v8 h8 v-8 h8 v8 h8 v-8" stroke="#5b8cff" strokeWidth="1.4" fill="none" />
      {/* BOOT button and status LED */}
      <g transform="translate(130 182)">
        <rect x="-13" y="-9" width="26" height="18" rx="4" fill="#132955" stroke="#2f4c86" />
        <circle r="6" fill="#dde5f2" />
        <text y="-14" textAnchor="middle" className="art-silk">
          BOOT
        </text>
      </g>
      <circle cx="206" cy="184" r="3.5" fill="#5b8cff" />
      {/* relay */}
      <g transform="translate(276 92)">
        <rect width="48" height="64" rx="6" fill={on ? '#132f6b' : '#0f2147'} stroke={on ? '#5b8cff' : '#2f4c86'} />
        <text x="24" y="22" textAnchor="middle" className="art-silk">
          RELAY
        </text>
        <rect x="10" y="32" width="28" height="14" rx="7" fill="#0a1733" stroke="#2f4c86" />
        <circle cx={on ? 31 : 17} cy="39" r="5" fill={on ? '#5b8cff' : '#4a5d80'} />
        <text x="24" y="58" textAnchor="middle" className={on ? 'art-silk on' : 'art-silk'}>
          {on ? 'LOAD ON' : 'LOAD OFF'}
        </text>
      </g>
      <rect x="160" y="190" width="40" height="12" rx="6" fill="#a7b3c7" stroke="#6b7a93" />
    </svg>
  )
}
