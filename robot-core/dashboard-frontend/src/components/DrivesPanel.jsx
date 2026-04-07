import { useRobotStore } from '../store/robotStore'

const DRIVES = [
  { key: 'energy',              label: 'Energia',  icon: '⚡', color: '#22c55e' },
  { key: 'social_drive',        label: 'Social',   icon: '👥', color: '#06b6d4' },
  { key: 'curiosity',           label: 'Curiosità',icon: '🔍', color: '#a855f7' },
  { key: 'attention',           label: 'Attenzione',icon: '👁️', color: '#eab308' },
  { key: 'interaction_fatigue', label: 'Stanchezza',icon: '😴', color: '#ef4444' },
]

export default function DrivesPanel() {
  const { drives } = useRobotStore()

  return (
    <div className="card space-y-3">
      <p className="card-title">🧠 Drive interni</p>
      {DRIVES.map(({ key, label, icon, color }) => {
        const pct = Math.round((drives[key] || 0) * 100)
        return (
          <div key={key}>
            <div className="flex justify-between text-xs mb-1">
              <span style={{ color: 'var(--color-muted)' }}>{icon} {label}</span>
              <span className="font-mono font-semibold text-white">{pct}%</span>
            </div>
            <div className="progress-bar">
              <div className="progress-fill" style={{ width: `${pct}%`, background: color }} />
            </div>
          </div>
        )
      })}
    </div>
  )
}
