import { useRobotStore } from '../store/robotStore'

const MOOD_CFG = {
  content:   { emoji: '😊', color: '#22c55e' },
  happy:     { emoji: '😄', color: '#22c55e' },
  excited:   { emoji: '🤩', color: '#eab308' },
  thinking:  { emoji: '🤔', color: '#06b6d4' },
  listening: { emoji: '👂', color: '#ef4444' },
  speaking:  { emoji: '🗣️', color: '#22c55e' },
  curious:   { emoji: '🧐', color: '#a855f7' },
  sleepy:    { emoji: '😴', color: '#475569' },
  confused:  { emoji: '😕', color: '#f97316' },
  stressed:  { emoji: '😰', color: '#ef4444' },
  surprised: { emoji: '😲', color: '#f97316' },
  bored:     { emoji: '😑', color: '#64748b' },
  wary:      { emoji: '😟', color: '#f97316' },
  tired:     { emoji: '😪', color: '#64748b' },
}

export default function MoodDisplay() {
  const { mood, drives } = useRobotStore()
  const cfg = MOOD_CFG[mood] ?? MOOD_CFG.content

  const bars = [
    { key: 'energy',       label: 'Energia',   color: '#22c55e' },
    { key: 'social_drive', label: 'Social',    color: '#06b6d4' },
    { key: 'curiosity',    label: 'Curiosità', color: '#a855f7' },
  ]

  return (
    <div className="card">
      <p className="card-title">💭 Umore</p>

      <div className="flex items-center gap-3 mb-4">
        <span className="text-4xl">{cfg.emoji}</span>
        <div>
          <div className="text-base font-bold capitalize text-white">{mood}</div>
          <div className="text-xs" style={{ color: 'var(--color-muted)' }}>stato corrente</div>
        </div>
      </div>

      <div className="space-y-2">
        {bars.map(({ key, label, color }) => {
          const pct = Math.round((drives[key] || 0) * 100)
          return (
            <div key={key}>
              <div className="flex justify-between text-xs mb-1">
                <span style={{ color: 'var(--color-muted)' }}>{label}</span>
                <span className="font-mono font-semibold text-white">{pct}%</span>
              </div>
              <div className="progress-bar">
                <div className="progress-fill" style={{ width: `${pct}%`, background: color }} />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
