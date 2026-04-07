import { useRobotStore } from '../store/robotStore'

export default function MemoryPanel() {
  const { facts } = useRobotStore()

  return (
    <div className="card" style={{ maxHeight: 280, overflowY: 'auto' }}>
      <p className="card-title sticky top-0 pb-2" style={{ background: 'var(--color-bg-card)' }}>
        🧬 Memoria semantica
        <span className="ml-auto text-xs font-mono" style={{ color: 'var(--color-muted)' }}>
          {facts.length} fatti
        </span>
      </p>

      {facts.length === 0 ? (
        <p className="text-sm py-4 text-center" style={{ color: 'var(--color-muted)' }}>
          Nessun fatto memorizzato
        </p>
      ) : (
        <div className="space-y-2">
          {facts.slice(0, 12).map((f, i) => (
            <div key={i} className="rounded-lg px-3 py-2"
                 style={{ background: '#0f172a', border: '1px solid var(--color-border)' }}>
              <div className="text-xs font-semibold text-cyan-400 truncate">{f.key}</div>
              <div className="text-xs text-white mt-0.5 truncate">{f.value}</div>
              <div className="text-xs font-mono mt-0.5 text-right" style={{ color: 'var(--color-muted)' }}>
                {(f.confidence * 100).toFixed(0)}%
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
