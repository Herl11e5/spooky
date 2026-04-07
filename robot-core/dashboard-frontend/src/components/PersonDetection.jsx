import { useRobotStore } from '../store/robotStore'

export default function PersonDetection() {
  const { detectedPerson } = useRobotStore()

  return (
    <div className="card">
      <p className="card-title">👤 Rilevamento persona</p>

      {!detectedPerson ? (
        <div className="flex items-center gap-2 py-2">
          <div className="status-dot off" />
          <span className="text-sm" style={{ color: 'var(--color-muted)' }}>Nessuna persona</span>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <div className="status-dot on" />
            <span className="text-base font-bold text-white">{detectedPerson.name}</span>
            <span className="badge ml-auto"
                  style={{ background: detectedPerson.known ? '#15803d20' : '#78350f20',
                           color: detectedPerson.known ? '#4ade80' : '#fbbf24',
                           border: `1px solid ${detectedPerson.known ? '#22c55e40' : '#f59e0b40'}` }}>
              {detectedPerson.known ? '✓ Conosciuto' : '? Sconosciuto'}
            </span>
          </div>
          <div>
            <div className="flex justify-between text-xs mb-1">
              <span style={{ color: 'var(--color-muted)' }}>Confidenza</span>
              <span className="font-mono text-white">
                {(detectedPerson.confidence * 100).toFixed(0)}%
              </span>
            </div>
            <div className="progress-bar">
              <div className="progress-fill"
                   style={{ width: `${detectedPerson.confidence * 100}%`, background: '#22c55e' }} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
