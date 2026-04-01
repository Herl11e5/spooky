import { useRef, useEffect, useState } from 'react'
import { api } from '../services/api'
import { Radar } from 'lucide-react'

export default function RadarScan() {
  const canvasRef = useRef(null)
  const [readings, setReadings] = useState([])
  const [scanning, setScanning] = useState(false)

  useEffect(() => {
    // Fetch initial scan
    api.getScan().then(data => {
      if (data.readings) setReadings(data.readings)
    })
  }, [])

  const handleStartScan = async () => {
    setScanning(true)
    try {
      await api.startScan()
    } catch (err) {
      console.error('Scan error:', err)
    } finally {
      setScanning(false)
    }
  }

  useEffect(() => {
    if (!canvasRef.current || !readings.length) return

    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d')
    const centerX = canvas.width / 2
    const centerY = canvas.height / 2
    const maxR = 80

    // Clear
    ctx.fillStyle = '#0d0d0d'
    ctx.fillRect(0, 0, canvas.width, canvas.height)

    // Grid circles
    ctx.strokeStyle = '#39ff1422'
    ctx.lineWidth = 1
    for (let r = 25; r <= maxR; r += 25) {
      ctx.beginPath()
      ctx.arc(centerX, centerY, r, 0, Math.PI * 2)
      ctx.stroke()
    }

    // Radial lines
    ctx.strokeStyle = '#39ff1411'
    for (let a = 0; a < 360; a += 30) {
      const rad = (a * Math.PI) / 180
      const x = centerX + Math.cos(rad) * maxR
      const y = centerY + Math.sin(rad) * maxR
      ctx.beginPath()
      ctx.moveTo(centerX, centerY)
      ctx.lineTo(x, y)
      ctx.stroke()
    }

    // Draw readings
    const n = readings.length
    const stepDeg = 360 / n
    readings.forEach((r, i) => {
      const dist = Math.min(r.dist >= 990 ? 200 : r.dist, 200)
      const frac = dist / 200
      const pr = frac * maxR
      const angle = (i * stepDeg - 90) * (Math.PI / 180)

      const x = centerX + Math.cos(angle) * pr
      const y = centerY + Math.sin(angle) * pr

      // Color based on distance
      const color = dist < 30 ? '#ff4444' : dist < 80 ? '#ffd60a' : '#2ecc71'

      // Point
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.arc(x, y, 3, 0, Math.PI * 2)
      ctx.fill()
    })

    // Robot center
    ctx.fillStyle = '#39ff14'
    ctx.beginPath()
    ctx.arc(centerX, centerY, 4, 0, Math.PI * 2)
    ctx.fill()
  }, [readings])

  return (
    <div className="border-2 border-spooky-neon-green rounded-lg p-4 bg-black/30">
      <h3 className="text-lg font-bold text-spooky-neon-green flex items-center gap-2 mb-3">
        <Radar className="w-5 h-5" />
        Radar Scan ({readings.length} readings)
      </h3>

      <canvas
        ref={canvasRef}
        width={220}
        height={220}
        className="w-full border border-spooky-neon-green/30 rounded mb-3"
      />

      <button
        onClick={handleStartScan}
        disabled={scanning}
        className="w-full px-3 py-2 bg-spooky-neon-green text-black font-bold rounded hover:opacity-80 disabled:opacity-50"
      >
        {scanning ? '🔄 Scanning...' : '📡 Start Scan'}
      </button>
    </div>
  )
}
