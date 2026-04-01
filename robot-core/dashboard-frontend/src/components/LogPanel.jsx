import { useRobotStore } from '../store/robotStore'
import { Activity } from 'lucide-react'

export default function LogPanel() {
  const { logs } = useRobotStore()

  return (
    <div className="border-2 border-spooky-neon-yellow rounded-lg p-4 bg-black/30 max-h-96 overflow-y-auto">
      <h3 className="text-lg font-bold text-spooky-neon-yellow flex items-center gap-2 mb-3 sticky top-0 bg-black/50 pb-2">
        <Activity className="w-5 h-5" />
        System Log
        <span className="ml-auto text-sm text-spooky-neon-cyan/50">
          ({logs.length})
        </span>
      </h3>

      {logs.length === 0 ? (
        <p className="text-spooky-neon-cyan/50 text-sm text-center py-4">
          No logs
        </p>
      ) : (
        <div className="space-y-1">
          {logs.slice(0, 50).map((log, idx) => (
            <div
              key={idx}
              className={`text-xs font-mono p-1 truncate ${
                log.includes('error')
                  ? 'text-spooky-neon-red'
                  : log.includes('warn')
                  ? 'text-spooky-neon-yellow'
                  : 'text-spooky-neon-cyan/70'
              }`}
            >
              {log}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
