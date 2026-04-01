import { useRobotStore } from '../store/robotStore'
import { User } from 'lucide-react'

export default function PersonDetection() {
  const { detectedPerson } = useRobotStore()

  if (!detectedPerson) {
    return (
      <div className="border-2 border-spooky-neon-pink rounded-lg p-4 bg-black/30">
        <h3 className="text-lg font-bold text-spooky-neon-pink flex items-center gap-2 mb-3">
          <User className="w-5 h-5" />
          Person Detected
        </h3>
        <p className="text-spooky-neon-cyan/50 text-center py-4">
          No one detected
        </p>
      </div>
    )
  }

  return (
    <div className="border-2 border-spooky-neon-pink rounded-lg p-4 bg-spooky-neon-pink/10">
      <h3 className="text-lg font-bold text-spooky-neon-pink flex items-center gap-2 mb-3">
        <User className="w-5 h-5" />
        👤 {detectedPerson.name}
      </h3>

      <div className="space-y-2">
        <div className="flex justify-between text-sm">
          <span className="text-spooky-neon-cyan">Confidence:</span>
          <span className="text-spooky-neon-green font-mono">
            {(detectedPerson.confidence * 100).toFixed(0)}%
          </span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-spooky-neon-cyan">Status:</span>
          <span className={detectedPerson.known ? 'text-spooky-neon-green' : 'text-spooky-neon-yellow'}>
            {detectedPerson.known ? '✓ Known' : '? Stranger'}
          </span>
        </div>
      </div>
    </div>
  )
}
