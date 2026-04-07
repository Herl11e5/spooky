import { useEffect, useRef } from 'react'
import { useRobotStore } from '../store/robotStore'

export default function CameraFeed() {
  const imgRef = useRef(null)
  const { scene, objects } = useRobotStore()

  useEffect(() => {
    if (!imgRef.current) return
    imgRef.current.src = '/camera'
  }, [])

  return (
    <div className="card p-0 overflow-hidden">
      <div className="relative" style={{ background: '#000', aspectRatio: '16/9' }}>
        <img
          ref={imgRef}
          alt="Camera"
          className="w-full h-full object-cover"
          onError={(e) => { setTimeout(() => { e.target.src = '/camera?t=' + Date.now() }, 3000) }}
        />
        {/* overlay top-left label */}
        <div className="absolute top-2 left-2">
          <span className="badge" style={{ background: '#00000080', color: '#06b6d4', border: '1px solid #06b6d460' }}>
            📷 LIVE
          </span>
        </div>
        {/* overlay bottom */}
        <div className="absolute bottom-0 left-0 right-0 px-3 py-2"
             style={{ background: 'linear-gradient(transparent, #00000090)' }}>
          {scene && (
            <p className="text-xs text-green-400 truncate mb-0.5">👁️ {scene}</p>
          )}
          {objects && (
            <p className="text-xs text-yellow-400 truncate">🔍 {objects}</p>
          )}
          {!scene && !objects && (
            <p className="text-xs" style={{ color: 'var(--color-muted)' }}>Nessuna analisi disponibile</p>
          )}
        </div>
      </div>
    </div>
  )
}
