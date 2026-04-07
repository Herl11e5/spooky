import { useRef, useEffect, useState } from 'react'
import { api } from '../services/api'

export default function RadarScan() {
  const canvasRef = useRef(null)
  const [readings, setReadings] = useState([])
  const [scanning, setScanning] = useState(false)

  useEffect(() => {
    api.getScan().then(d => { if (d.readings) setReadings(d.readings) })
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    const cx = canvas.width / 2
    const cy = canvas.height / 2
    const maxR = canvas.width / 2 - 8

    ctx.fillStyle = '#0f172a'
    ctx.fillRect(0, 0, canvas.width, canvas.height)

    // Grid
    ctx.strokeStyle = '#1e293b'
    ctx.lineWidth = 1
    for (let r = maxR / 3; r <= maxR; r += maxR / 3) {
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke()
    }
    for (let a = 0; a < 360; a += 45) {
      const rad = (a * Math.PI) / 180
      ctx.beginPath()
      ctx.moveTo(cx, cy)
      ctx.lineTo(cx + Math.cos(rad) * maxR, cy + Math.sin(rad) * maxR)
      ctx.stroke()
    }

    if (!readings.length) {
      ctx.fillStyle = '#334155'
      ctx.font = '11px monospace'
      ctx.textAlign = 'center'
      ctx.fillText('Nessun dato', cx, cy + 4)
      return
    }

    const n = readings.length
    readings.forEach((r, i) => {
      const dist = Math.min(r.dist >= 990 ? 200 : r.dist, 200)
      const pr = (dist / 200) * maxR
      const angle = ((i / n) * 360 - 90) * (Math.PI / 180)
      const x = cx + Math.cos(angle) * pr
      const y = cy + Math.sin(angle) * pr
      const col = dist < 30 ? '#ef4444' : dist < 80 ? '#eab308' : '#22c55e'
      ctx.fillStyle = col
      ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill()
    })

    // Robot center
    ctx.fillStyle = '#22c55e'
    ctx.beginPath(); ctx.arc(cx, cy, 4, 0, Math.PI * 2); ctx.fill()
  }, [readings])

  const startScan = async () => {
    setScanning(true)
    try { await api.startScan() } catch (e) { console.error(e) }
    finally { setScanning(false) }
  }

  return (
    <div className="card">
      <p className="card-title">📡 Radar ({readings.length} letture)</p>
      <canvas ref={canvasRef} width={200} height={200}
              className="w-full rounded-lg mb-3"
              style={{ border: '1px solid var(--color-border)' }} />
      <button onClick={startScan} disabled={scanning} className="btn btn-green w-full">
        {scanning ? '🔄 Scanning…' : '📡 Avvia scansione'}
      </button>
    </div>
  )
}
