import { useRobotStore } from '../store/robotStore'

const EXPRESSIONS = {
  content: {
    mouth: 'M 40 82 Q 60 92 80 82',
    pupilR: 5,
    eyeColor: '#39ff14',
    blush: 0,
  },
  happy: {
    mouth: 'M 35 78 Q 60 96 85 78',
    pupilR: 6,
    eyeColor: '#39ff14',
    blush: 0.3,
  },
  excited: {
    mouth: 'M 35 75 Q 60 98 85 75',
    pupilR: 7,
    eyeColor: '#ffd60a',
    blush: 0.5,
  },
  thinking: {
    mouth: 'M 45 85 Q 60 85 75 85',
    pupilR: 4,
    eyeColor: '#00c8ff',
    blush: 0,
  },
  listening: {
    mouth: 'M 42 84 Q 60 90 78 84',
    pupilR: 6,
    eyeColor: '#ff4444',
    blush: 0,
  },
  speaking: {
    mouth: 'M 42 80 Q 60 95 78 80',
    pupilR: 5,
    eyeColor: '#39ff14',
    blush: 0.1,
  },
  curious: {
    mouth: 'M 42 84 Q 55 90 78 80',
    pupilR: 5,
    eyeColor: '#c77dff',
    blush: 0,
  },
  sleepy: {
    mouth: 'M 45 86 Q 60 84 75 86',
    pupilR: 3,
    eyeColor: '#555',
    blush: 0,
  },
}

export default function RobotFace() {
  const { micState, ttsSpeak, mode } = useRobotStore()

  let expression = 'content'
  if (ttsSpeak) expression = 'speaking'
  else if (micState === 'listening') expression = 'listening'
  else if (micState === 'thinking') expression = 'thinking'
  else if (mode === 'idle_observer') expression = 'curious'
  else if (mode === 'night_watch') expression = 'sleepy'

  const expr = EXPRESSIONS[expression] || EXPRESSIONS.content

  return (
    <div className="bg-black border-2 border-spooky-neon-green rounded-lg p-6 flex flex-col items-center justify-center h-64">
      <svg viewBox="0 0 120 120" className="w-40 h-40 mb-4" xmlns="http://www.w3.org/2000/svg">
        {/* Head */}
        <rect x="20" y="30" width="80" height="65" rx="15" fill="#1a1a1a" stroke="#39ff14" strokeWidth="2" />

        {/* Eyes */}
        <circle cx="42" cy="58" r="12" fill="#0d0d0d" stroke={expr.eyeColor} strokeWidth="2" />
        <circle cx="78" cy="58" r="12" fill="#0d0d0d" stroke={expr.eyeColor} strokeWidth="2" />

        {/* Pupils */}
        <circle cx="42" cy="58" r={expr.pupilR} fill={expr.eyeColor} />
        <circle cx="78" cy="58" r={expr.pupilR} fill={expr.eyeColor} />

        {/* Mouth */}
        <path d={expr.mouth} fill="none" stroke="#39ff14" strokeWidth="2.5" strokeLinecap="round" />

        {/* Antenna */}
        <line x1="60" y1="30" x2="60" y2="15" stroke="#39ff14" strokeWidth="2" />
        <circle cx="60" cy="12" r="4" fill="#39ff14" className="animate-pulse-neon" />

        {/* Blush */}
        <ellipse cx="30" cy="72" rx="8" ry="5" fill="#ff69b4" opacity={expr.blush} />
        <ellipse cx="90" cy="72" rx="8" ry="5" fill="#ff69b4" opacity={expr.blush} />
      </svg>

      <div className="text-sm text-spooky-neon-cyan">
        Expression: <span className="text-spooky-neon-green font-bold capitalize">{expression}</span>
      </div>
    </div>
  )
}
