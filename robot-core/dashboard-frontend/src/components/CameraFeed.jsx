import { useEffect, useRef } from 'react'
import { useRobotStore } from '../store/robotStore'
import { Video } from 'lucide-react'

export default function CameraFeed() {
  const imgRef = useRef(null)
  const { scene, objects } = useRobotStore()

  useEffect(() => {
    if (!imgRef.current) return
    // MJPEG stream — set src once, the browser handles the continuous stream
    imgRef.current.src = '/camera'
  }, [])

  return (
    <div className="border-2 border-spooky-neon-cyan rounded-lg overflow-hidden bg-black">
      <div className="relative aspect-video bg-black flex items-center justify-center">
        <img
          ref={imgRef}
          alt="Camera Feed"
          className="w-full h-full object-cover"
          onError={(e) => {
            setTimeout(() => { e.target.src = '/camera?t=' + Date.now() }, 3000)
          }}
        />
        <Video className="absolute top-4 left-4 text-spooky-neon-cyan opacity-60" />

        {/* Overlay Info */}
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black to-transparent p-4 text-sm space-y-1">
          {scene && (
            <div className="text-spooky-neon-green">
              👁️ {scene}
            </div>
          )}
          {objects && (
            <div className="text-spooky-neon-yellow">
              🔍 Objects: {objects}
            </div>
          )}
          {!scene && !objects && (
            <div className="text-spooky-neon-cyan/60">
              No scene analysis available
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
