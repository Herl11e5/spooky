import { useRobotStore } from '../store/robotStore'
import { api } from '../services/api'
import { Gamepad2 } from 'lucide-react'

export default function ModeButtons() {
  const { mode, setMode } = useRobotStore()

  const modes = [
    { id: 'companion_day', label: '🌞 Companion', desc: 'Social & playful' },
    { id: 'focus_assistant', label: '🎯 Focus', desc: 'Productive mode' },
    { id: 'idle_observer', label: '👁️ Observer', desc: 'Curious explorer' },
    { id: 'night_watch', label: '🌙 Night', desc: 'Silent watcher' },
  ]

  const handleMode = async (m) => {
    setMode(m)
    try {
      await api.setMode(m)
    } catch (err) {
      console.error('Mode change error:', err)
    }
  }

  return (
    <div className="border-2 border-spooky-neon-yellow rounded-lg p-4 bg-black/30">
      <h3 className="text-lg font-bold text-spooky-neon-yellow flex items-center gap-2 mb-3">
        <Gamepad2 className="w-5 h-5" />
        Modes
      </h3>

      <div className="grid grid-cols-2 gap-2">
        {modes.map(({ id, label, desc }) => (
          <button
            key={id}
            onClick={() => handleMode(id)}
            className={`p-3 rounded font-bold text-sm transition-all ${
              mode === id
                ? 'bg-spooky-neon-yellow text-black border-2 border-spooky-neon-yellow'
                : 'bg-black/50 text-spooky-neon-yellow border-2 border-spooky-neon-yellow/30 hover:border-spooky-neon-yellow'
            }`}
          >
            <div>{label}</div>
            <div className="text-xs opacity-70 font-normal">{desc}</div>
          </button>
        ))}
      </div>
    </div>
  )
}
