import { useRobotStore } from '../store/robotStore'
import { Brain } from 'lucide-react'

export default function MemoryPanel() {
  const { facts } = useRobotStore()

  return (
    <div className="border-2 border-spooky-neon-purple rounded-lg p-4 bg-black/30 max-h-96 overflow-y-auto">
      <h3 className="text-lg font-bold text-spooky-neon-purple flex items-center gap-2 mb-3 sticky top-0 bg-black/50 pb-2">
        <Brain className="w-5 h-5" />
        Semantic Memory
      </h3>

      {facts.length === 0 ? (
        <p className="text-spooky-neon-cyan/50 text-sm text-center py-4">
          No facts stored yet
        </p>
      ) : (
        <div className="space-y-2">
          {facts.slice(0, 10).map((fact, idx) => (
            <div key={idx} className="text-xs bg-black/50 p-2 rounded border border-spooky-neon-purple/30">
              <div className="font-bold text-spooky-neon-cyan truncate">
                {fact.key}
              </div>
              <div className="text-spooky-neon-purple/80 truncate">
                {fact.value}
              </div>
              <div className="text-spooky-neon-green/60 text-right">
                {(fact.confidence * 100).toFixed(0)}%
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
