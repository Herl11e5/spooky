import { Zap } from 'lucide-react'
import { api } from '../services/api'

export default function MotorControl() {
  const handleMotor = (action) => {
    api.motorCmd(action)
  }

  return (
    <div className="border-2 border-spooky-neon-yellow rounded-lg p-4 bg-black/30">
      <h3 className="text-lg font-bold text-spooky-neon-yellow flex items-center gap-2 mb-4">
        <Zap className="w-5 h-5" />
        Motori
      </h3>

      {/* Direction Pad */}
      <div className="grid grid-cols-3 gap-2 w-fit mx-auto mb-4">
        <div />
        <button
          onMouseDown={() => handleMotor('forward')}
          onMouseUp={() => handleMotor('stop')}
          onTouchStart={() => handleMotor('forward')}
          onTouchEnd={() => handleMotor('stop')}
          className="w-12 h-12 bg-spooky-neon-yellow text-black rounded font-bold active:opacity-70"
        >
          ▲
        </button>
        <div />

        <button
          onMouseDown={() => handleMotor('left')}
          onMouseUp={() => handleMotor('stop')}
          onTouchStart={() => handleMotor('left')}
          onTouchEnd={() => handleMotor('stop')}
          className="w-12 h-12 bg-spooky-neon-cyan text-black rounded font-bold active:opacity-70"
        >
          ◀
        </button>
        <button
          onClick={() => handleMotor('stop')}
          className="w-12 h-12 bg-spooky-neon-red text-white rounded font-bold"
        >
          ■
        </button>
        <button
          onMouseDown={() => handleMotor('right')}
          onMouseUp={() => handleMotor('stop')}
          onTouchStart={() => handleMotor('right')}
          onTouchEnd={() => handleMotor('stop')}
          className="w-12 h-12 bg-spooky-neon-cyan text-black rounded font-bold active:opacity-70"
        >
          ▶
        </button>

        <div />
        <button
          onMouseDown={() => handleMotor('backward')}
          onMouseUp={() => handleMotor('stop')}
          onTouchStart={() => handleMotor('backward')}
          onTouchEnd={() => handleMotor('stop')}
          className="w-12 h-12 bg-spooky-neon-yellow text-black rounded font-bold active:opacity-70"
        >
          ▼
        </button>
        <div />
      </div>

      {/* Action Buttons */}
      <div className="grid grid-cols-2 gap-2">
        <button
          onClick={() => handleMotor('wave')}
          className="px-3 py-2 bg-spooky-neon-purple text-white rounded text-sm font-bold hover:opacity-80"
        >
          👋 Wave
        </button>
        <button
          onClick={() => handleMotor('center')}
          className="px-3 py-2 bg-spooky-neon-purple text-white rounded text-sm font-bold hover:opacity-80"
        >
          🎯 Center
        </button>
      </div>
    </div>
  )
}
