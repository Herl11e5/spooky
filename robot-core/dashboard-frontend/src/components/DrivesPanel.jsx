import { useRobotStore } from '../store/robotStore'
import { Zap } from 'lucide-react'

export default function DrivesPanel() {
  const { drives } = useRobotStore()

  const driveList = [
    { key: 'energy', label: 'Energy', icon: '⚡', color: 'bg-spooky-neon-green' },
    { key: 'social_drive', label: 'Social', icon: '👥', color: 'bg-spooky-neon-cyan' },
    { key: 'curiosity', label: 'Curiosity', icon: '🔍', color: 'bg-spooky-neon-purple' },
    { key: 'attention', label: 'Attention', icon: '👁️', color: 'bg-spooky-neon-yellow' },
    { key: 'interaction_fatigue', label: 'Fatigue', icon: '😴', color: 'bg-spooky-neon-red' },
  ]

  return (
    <div className="border-2 border-spooky-neon-green rounded-lg p-4 bg-black/30">
      <h3 className="text-lg font-bold text-spooky-neon-green flex items-center gap-2 mb-3">
        <Zap className="w-5 h-5" />
        Internal Drives
      </h3>

      <div className="space-y-2">
        {driveList.map(({ key, label, icon, color }) => {
          const value = drives[key] || 0
          const pct = Math.round(value * 100)
          return (
            <div key={key}>
              <div className="flex justify-between items-center mb-1">
                <label className="text-sm text-spooky-neon-cyan">
                  {icon} {label}
                </label>
                <span className="text-xs font-mono text-spooky-neon-green">
                  {pct}%
                </span>
              </div>
              <div className="h-2 bg-black/50 rounded border border-spooky-neon-green/30">
                <div
                  className={`h-full rounded ${color} transition-all`}
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
