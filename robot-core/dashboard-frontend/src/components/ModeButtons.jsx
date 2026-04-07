import { useRobotStore } from '../store/robotStore'
import { api } from '../services/api'

const MODES = [
  { id: 'companion_day',   icon: '🌞', label: 'Companion',  color: '#22c55e' },
  { id: 'focus_assistant', icon: '🎯', label: 'Focus',      color: '#06b6d4' },
  { id: 'idle_observer',   icon: '👁️', label: 'Observer',   color: '#a855f7' },
  { id: 'night_watch',     icon: '🌙', label: 'Night Watch',color: '#eab308' },
]

export default function ModeButtons() {
  const { mode, setMode } = useRobotStore()

  const handleMode = async (id) => {
    setMode(id)
    try { await api.setMode(id) } catch (e) { console.error(e) }
  }

  return (
    <div className="card">
      <p className="card-title">⚙️ Modalità</p>
      <div className="grid grid-cols-2 gap-2">
        {MODES.map(({ id, icon, label, color }) => {
          const active = mode === id
          return (
            <button key={id} onClick={() => handleMode(id)}
                    className="rounded-lg px-3 py-3 text-sm font-semibold text-left transition-all"
                    style={{
                      background: active ? `${color}18` : '#0f172a',
                      border: `1px solid ${active ? color : 'var(--color-border)'}`,
                      color: active ? color : 'var(--color-muted)',
                    }}>
              <div className="text-base">{icon}</div>
              <div className="mt-0.5">{label}</div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
