import { useRobotStore } from '../store/robotStore'

export default function LLMStream() {
  const { llmCalls } = useRobotStore()

  return (
    <div className="card flex flex-col" style={{ height: 340 }}>
      <p className="card-title">🤖 Chiamate LLM
        <span className="ml-auto text-xs font-mono" style={{ color: 'var(--color-muted)' }}>
          {llmCalls.length}
        </span>
      </p>

      <div className="flex-1 overflow-y-auto space-y-2 pr-1">
        {llmCalls.length === 0 ? (
          <p className="text-sm text-center py-6" style={{ color: 'var(--color-muted)' }}>
            Nessuna chiamata
          </p>
        ) : llmCalls.slice(0, 20).map((c, i) => (
          <div key={i} className="rounded-lg px-3 py-2 text-xs"
               style={{ background: '#0f172a', border: '1px solid var(--color-border)' }}>
            <div className="flex justify-between items-center mb-1">
              <span className="font-semibold text-cyan-400 truncate">{c.trigger || '—'}</span>
              <span className="font-mono ml-2 flex-shrink-0"
                    style={{ color: c.fallback ? '#ef4444' : '#22c55e' }}>
                {c.fallback ? '⚠ FALLBACK' : '✓ LLM'} {c.time_ms}ms
              </span>
            </div>
            {c.prompt && (
              <p className="text-yellow-300 truncate mb-0.5">❓ {c.prompt}</p>
            )}
            <p className="text-purple-300 truncate">💬 {c.reply || '—'}</p>
            <p className="text-right mt-1 font-mono" style={{ color: 'var(--color-muted)' }}>
              {c.model}
            </p>
          </div>
        ))}
      </div>
    </div>
  )
}
