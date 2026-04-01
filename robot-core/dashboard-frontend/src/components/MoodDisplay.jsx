import { useRobotStore } from '../store/robotStore'

const MOOD_EMOJIS = {
  content: '😊',
  happy: '😄',
  excited: '🤩',
  thinking: '🤔',
  listening: '👂',
  speaking: '🗣️',
  curious: '🤨',
  sleepy: '😴',
  confused: '😕',
  stressed: '😰',
}

export default function MoodDisplay() {
  const { mood, drives } = useRobotStore()
  const emoji = MOOD_EMOJIS[mood] || '😊'

  return (
    <div className="bg-gradient-to-br from-spooky-neon-purple/20 to-spooky-neon-cyan/20 border-2 border-spooky-neon-purple rounded-lg p-6 flex flex-col items-center justify-center h-64">
      <div className="text-6xl mb-4">{emoji}</div>
      <h3 className="text-2xl font-bold text-spooky-neon-purple capitalize mb-4">{mood}</h3>
      
      {/* Mini drive indicators */}
      <div className="w-full space-y-2">
        <div className="flex items-center justify-between text-xs">
          <span className="text-spooky-neon-cyan">Energy</span>
          <div className="w-20 h-2 bg-black/50 rounded border border-spooky-neon-green/30">
            <div
              className="h-full bg-spooky-neon-green rounded"
              style={{ width: `${(drives.energy || 0) * 100}%` }}
            />
          </div>
        </div>
        <div className="flex items-center justify-between text-xs">
          <span className="text-spooky-neon-cyan">Social</span>
          <div className="w-20 h-2 bg-black/50 rounded border border-spooky-neon-cyan/30">
            <div
              className="h-full bg-spooky-neon-cyan rounded"
              style={{ width: `${(drives.social_drive || 0) * 100}%` }}
            />
          </div>
        </div>
        <div className="flex items-center justify-between text-xs">
          <span className="text-spooky-neon-cyan">Curiosity</span>
          <div className="w-20 h-2 bg-black/50 rounded border border-spooky-neon-purple/30">
            <div
              className="h-full bg-spooky-neon-purple rounded"
              style={{ width: `${(drives.curiosity || 0) * 100}%` }}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
