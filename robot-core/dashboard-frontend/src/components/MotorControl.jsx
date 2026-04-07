import { api } from '../services/api'

const BTN = 'w-11 h-11 rounded-lg font-bold text-sm flex items-center justify-center select-none cursor-pointer active:scale-95 transition-transform'

export default function MotorControl() {
  const motor = (action) => api.motorCmd(action)

  return (
    <div className="card">
      <p className="card-title">🕹️ Motori</p>

      {/* D-pad */}
      <div className="grid grid-cols-3 gap-2 w-fit mx-auto mb-3">
        <div />
        <button className={BTN}
          style={{ background: '#1e3a5f', color: '#93c5fd', border: '1px solid #3b82f620' }}
          onMouseDown={() => motor('forward')} onMouseUp={() => motor('stop')}
          onTouchStart={() => motor('forward')} onTouchEnd={() => motor('stop')}>
          ▲
        </button>
        <div />

        <button className={BTN}
          style={{ background: '#1e3a5f', color: '#93c5fd', border: '1px solid #3b82f620' }}
          onMouseDown={() => motor('left')} onMouseUp={() => motor('stop')}
          onTouchStart={() => motor('left')} onTouchEnd={() => motor('stop')}>
          ◀
        </button>
        <button className={BTN}
          style={{ background: '#3b1e1e', color: '#fca5a5', border: '1px solid #ef444440' }}
          onClick={() => motor('stop')}>
          ■
        </button>
        <button className={BTN}
          style={{ background: '#1e3a5f', color: '#93c5fd', border: '1px solid #3b82f620' }}
          onMouseDown={() => motor('right')} onMouseUp={() => motor('stop')}
          onTouchStart={() => motor('right')} onTouchEnd={() => motor('stop')}>
          ▶
        </button>

        <div />
        <button className={BTN}
          style={{ background: '#1e3a5f', color: '#93c5fd', border: '1px solid #3b82f620' }}
          onMouseDown={() => motor('backward')} onMouseUp={() => motor('stop')}
          onTouchStart={() => motor('backward')} onTouchEnd={() => motor('stop')}>
          ▼
        </button>
        <div />
      </div>

      {/* Actions */}
      <div className="grid grid-cols-2 gap-2">
        <button className="btn btn-ghost text-purple-400" onClick={() => motor('wave')}>
          👋 Wave
        </button>
        <button className="btn btn-ghost text-purple-400" onClick={() => motor('center')}>
          🎯 Center
        </button>
      </div>
    </div>
  )
}
