import { useRobotStore } from '../store/robotStore'

function Gauge({ label, value, max, unit, warn, danger, invert = false }) {
  const pct = Math.min((value / max) * 100, 100)
  const bad   = invert ? (danger && value < danger) : (danger && value > danger)
  const caution = invert ? (warn && value < warn)   : (warn   && value > warn)
  const color = bad ? '#ef4444' : caution ? '#eab308' : '#22c55e'

  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 min-w-0">
        <div className="flex justify-between text-xs mb-1">
          <span style={{ color: 'var(--color-muted)' }}>{label}</span>
          <span className="font-mono font-semibold" style={{ color }}>
            {typeof value === 'number' ? value.toFixed(1) : value}{unit}
          </span>
        </div>
        <div className="progress-bar">
          <div className="progress-fill" style={{ width: `${pct}%`, background: color }} />
        </div>
      </div>
    </div>
  )
}

export default function SensorPanel() {
  const { distance, temperature, ramUsage, pitch, roll } = useRobotStore()
  const edge = Math.abs(pitch) > 10 || Math.abs(roll) > 10

  return (
    <div className="card space-y-3">
      <p className="card-title">📡 Sensori</p>

      <Gauge label="Distanza"    value={distance >= 990 ? 200 : distance} max={200} unit=" cm" warn={40} danger={20} />
      <Gauge label="Temperatura" value={temperature} max={80}   unit="°C"  warn={65} danger={75} />
      <Gauge label="RAM libera"  value={ramUsage}    max={2000} unit=" MB" warn={400} danger={200} invert />

      <div className="grid grid-cols-2 gap-2 pt-2" style={{ borderTop: '1px solid var(--color-border)' }}>
        {[['Pitch', pitch], ['Roll', roll]].map(([n, v]) => {
          const alert = Math.abs(v) > 10
          return (
            <div key={n} className="rounded-lg px-3 py-2 text-center"
                 style={{ background: alert ? '#ef444415' : '#0f172a', border: `1px solid ${alert ? '#ef4444' : 'var(--color-border)'}` }}>
              <div className="text-xs" style={{ color: 'var(--color-muted)' }}>{n}</div>
              <div className="text-sm font-mono font-bold" style={{ color: alert ? '#ef4444' : '#f1f5f9' }}>
                {v.toFixed(1)}°
              </div>
            </div>
          )
        })}
      </div>

      {edge && (
        <div className="rounded-lg px-3 py-2 text-sm font-semibold text-red-400"
             style={{ background: '#ef444415', border: '1px solid #ef4444' }}>
          ⚠️ Bordo rilevato!
        </div>
      )}
    </div>
  )
}
