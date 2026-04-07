import { useRobotStore } from '../store/robotStore'

const EXPRESSIONS = {
  content:   { mouth: 'M 40 82 Q 60 92 80 82',  pr: 5, eye: '#22c55e', blush: 0    },
  happy:     { mouth: 'M 35 78 Q 60 96 85 78',  pr: 6, eye: '#22c55e', blush: 0.35 },
  excited:   { mouth: 'M 35 75 Q 60 98 85 75',  pr: 7, eye: '#eab308', blush: 0.55 },
  thinking:  { mouth: 'M 45 85 Q 60 85 75 85',  pr: 4, eye: '#06b6d4', blush: 0    },
  listening: { mouth: 'M 42 84 Q 60 90 78 84',  pr: 6, eye: '#ef4444', blush: 0    },
  speaking:  { mouth: 'M 42 80 Q 60 95 78 80',  pr: 5, eye: '#22c55e', blush: 0.1  },
  curious:   { mouth: 'M 42 84 Q 55 90 78 80',  pr: 5, eye: '#a855f7', blush: 0    },
  sleepy:    { mouth: 'M 45 86 Q 60 84 75 86',  pr: 3, eye: '#475569', blush: 0    },
  surprised: { mouth: 'M 50 82 Q 60 97 70 82',  pr: 8, eye: '#f97316', blush: 0.2  },
}

export default function RobotFace() {
  const { micState, ttsSpeak, mode } = useRobotStore()

  let expression = 'content'
  if (ttsSpeak)                      expression = 'speaking'
  else if (micState === 'listening') expression = 'listening'
  else if (micState === 'thinking')  expression = 'thinking'
  else if (mode === 'idle_observer') expression = 'curious'
  else if (mode === 'night_watch')   expression = 'sleepy'

  const e = EXPRESSIONS[expression] ?? EXPRESSIONS.content

  return (
    <div className="card flex flex-col items-center">
      <p className="card-title">🤖 Faccia</p>
      <svg viewBox="0 0 120 120" className="w-28 h-28" xmlns="http://www.w3.org/2000/svg">
        <rect x="20" y="25" width="80" height="70" rx="14"
              fill="#1e293b" stroke={e.eye} strokeWidth="1.5" />
        <circle cx="20" cy="60" r="4" fill="#334155" stroke={e.eye} strokeWidth="1" />
        <circle cx="100" cy="60" r="4" fill="#334155" stroke={e.eye} strokeWidth="1" />
        <circle cx="43" cy="55" r="13" fill="#0f172a" stroke={e.eye} strokeWidth="1.5" />
        <circle cx="77" cy="55" r="13" fill="#0f172a" stroke={e.eye} strokeWidth="1.5" />
        <circle cx="43" cy="55" r={e.pr} fill={e.eye} />
        <circle cx="77" cy="55" r={e.pr} fill={e.eye} />
        <circle cx={43 + e.pr * 0.4} cy={55 - e.pr * 0.4} r={e.pr * 0.28} fill="white" opacity="0.8" />
        <circle cx={77 + e.pr * 0.4} cy={55 - e.pr * 0.4} r={e.pr * 0.28} fill="white" opacity="0.8" />
        <path d={e.mouth} fill="none" stroke={e.eye} strokeWidth="2.5" strokeLinecap="round" />
        <line x1="60" y1="25" x2="60" y2="10" stroke={e.eye} strokeWidth="1.5" />
        <circle cx="60" cy="7" r="3.5" fill={e.eye} />
        {e.blush > 0 && <>
          <ellipse cx="28" cy="70" rx="7" ry="4" fill="#ec4899" opacity={e.blush} />
          <ellipse cx="92" cy="70" rx="7" ry="4" fill="#ec4899" opacity={e.blush} />
        </>}
      </svg>
      <div className="mt-2 text-sm font-semibold capitalize" style={{ color: e.eye }}>
        {expression}
      </div>
    </div>
  )
}
