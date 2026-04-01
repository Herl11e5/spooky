import { useRobotStore } from '../store/robotStore'
import { Gauge, Thermometer, HardDrive } from 'lucide-react'

function SensorGauge({ label, value, max, icon: Icon, unit, warning, danger, invert = false }) {
  const percentage = (value / max) * 100
  let color = 'text-spooky-neon-green'
  let bgColor = 'bg-spooky-neon-green'

  const isDanger = invert ? (danger && value < danger) : (danger && value > danger)
  const isWarning = invert ? (warning && value < warning) : (warning && value > warning)

  if (isDanger) {
    color = 'text-spooky-neon-red'
    bgColor = 'bg-spooky-neon-red'
  } else if (isWarning) {
    color = 'text-spooky-neon-yellow'
    bgColor = 'bg-spooky-neon-yellow'
  }

  return (
    <div className="flex items-center gap-3">
      <Icon className={`w-5 h-5 ${color}`} />
      <div className="flex-1">
        <div className="text-xs text-spooky-neon-cyan mb-1 font-bold">{label}</div>
        <div className="h-2 bg-black/50 rounded border border-spooky-neon-green/30">
          <div
            className={`h-full rounded ${bgColor} transition-all`}
            style={{ width: `${Math.min(percentage, 100)}%` }}
          />
        </div>
      </div>
      <div className={`text-sm font-mono ${color}`}>
        {value.toFixed(1)}{unit}
      </div>
    </div>
  )
}

export default function SensorPanel() {
  const { distance, temperature, ramUsage, pitch, roll } = useRobotStore()

  const edgeDetected = Math.abs(pitch) > 10 || Math.abs(roll) > 10

  return (
    <div className="border-2 border-spooky-neon-cyan rounded-lg p-4 bg-black/30 space-y-3">
      <h3 className="text-lg font-bold text-spooky-neon-cyan flex items-center gap-2">
        <Gauge className="w-5 h-5" />
        Sensori
      </h3>

      <SensorGauge
        label="Distanza"
        value={distance >= 990 ? 200 : distance}
        max={200}
        icon={Gauge}
        unit=" cm"
        warning={40}
        danger={20}
      />

      <SensorGauge
        label="Temperatura"
        value={temperature}
        max={80}
        icon={Thermometer}
        unit="°C"
        warning={65}
        danger={75}
      />

      <SensorGauge
        label="RAM libera"
        value={ramUsage}
        max={2000}
        icon={HardDrive}
        unit=" MB"
        warning={400}
        danger={200}
        invert
      />

      {/* Pitch & Roll */}
      <div className="grid grid-cols-2 gap-2 pt-2 border-t border-spooky-neon-cyan/20">
        <div className={`text-center p-2 rounded ${Math.abs(pitch) > 10 ? 'bg-spooky-neon-red/20 border border-spooky-neon-red' : 'bg-black/50 border border-spooky-neon-cyan/30'}`}>
          <div className="text-xs text-spooky-neon-cyan">Pitch</div>
          <div className={Math.abs(pitch) > 10 ? 'text-spooky-neon-red font-bold' : 'text-spooky-neon-green font-mono'}>
            {pitch.toFixed(1)}°
          </div>
        </div>
        <div className={`text-center p-2 rounded ${Math.abs(roll) > 10 ? 'bg-spooky-neon-red/20 border border-spooky-neon-red' : 'bg-black/50 border border-spooky-neon-cyan/30'}`}>
          <div className="text-xs text-spooky-neon-cyan">Roll</div>
          <div className={Math.abs(roll) > 10 ? 'text-spooky-neon-red font-bold' : 'text-spooky-neon-green font-mono'}>
            {roll.toFixed(1)}°
          </div>
        </div>
      </div>

      {edgeDetected && (
        <div className="bg-spooky-neon-red/20 border-l-4 border-spooky-neon-red p-2 text-sm text-spooky-neon-red">
          ⚠️ Edge detected!
        </div>
      )}
    </div>
  )
}
