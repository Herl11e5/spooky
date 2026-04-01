import { useRobotStore } from '../store/robotStore'
import { Code2 } from 'lucide-react'

export default function LLMStream() {
  const { llmCalls } = useRobotStore()

  return (
    <div className="border-2 border-spooky-neon-cyan rounded-lg p-4 bg-black/30 max-h-96 overflow-y-auto">
      <h3 className="text-lg font-bold text-spooky-neon-cyan flex items-center gap-2 mb-3 sticky top-0 bg-black/50 pb-2">
        <Code2 className="w-5 h-5" />
        LLM Calls
      </h3>

      {llmCalls.length === 0 ? (
        <p className="text-spooky-neon-cyan/50 text-sm text-center py-4">
          No LLM calls yet
        </p>
      ) : (
        <div className="space-y-2">
          {llmCalls.slice(0, 15).map((call, idx) => (
            <div key={idx} className="text-xs bg-black/50 p-2 rounded border-l-2 border-spooky-neon-cyan">
              <div className="flex justify-between mb-1">
                <span className="font-bold text-spooky-neon-cyan">
                  {call.trigger}
                </span>
                <span className={call.fallback ? 'text-spooky-neon-red' : 'text-spooky-neon-green'}>
                  {call.fallback ? 'FALLBACK' : 'LLM'} {call.time_ms}ms
                </span>
              </div>
              {call.prompt && (
                <div className="text-spooky-neon-yellow/80 mb-1 truncate">
                  Q: {call.prompt}
                </div>
              )}
              <div className="text-spooky-neon-purple/80 truncate">
                A: {call.reply || '—'}
              </div>
              <div className="text-spooky-neon-cyan/50 text-right mt-1">
                {call.model}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
