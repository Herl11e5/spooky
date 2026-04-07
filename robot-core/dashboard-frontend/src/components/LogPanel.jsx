import { useRobotStore } from '../store/robotStore'

export default function LogPanel() {
  const { logs } = useRobotStore()

  const color = (log) => {
    if (log.toLowerCase().includes('error') || log.includes('[ALERT')) return '#ef4444'
    if (log.toLowerCase().includes('warn'))  return '#eab308'
    if (log.includes('[person'))             return '#22c55e'
    if (log.includes('[tts'))                return '#a855f7'
    return '#94a3b8'
  }

  return (
    <div className="card flex flex-col" style={{ height: 340 }}>
      <p className="card-title">📋 Log sistema
        <span className="ml-auto text-xs font-mono" style={{ color: 'var(--color-muted)' }}>
          {logs.length}
        </span>
      </p>

      <div className="flex-1 overflow-y-auto space-y-0.5 pr-1">
        {logs.length === 0 ? (
          <p className="text-sm text-center py-6" style={{ color: 'var(--color-muted)' }}>
            Nessun log
          </p>
        ) : logs.slice(0, 60).map((log, i) => (
          <div key={i} className="text-xs font-mono px-2 py-0.5 rounded truncate"
               style={{ color: color(log), background: i % 2 === 0 ? '#0a1120' : 'transparent' }}>
            {log}
          </div>
        ))}
      </div>
    </div>
  )
}
