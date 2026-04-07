import { useState, useEffect } from 'react'
import { useRobotStore } from '../store/robotStore'
import RobotFace from './RobotFace'
import CameraFeed from './CameraFeed'
import SensorPanel from './SensorPanel'
import MoodDisplay from './MoodDisplay'
import MotorControl from './MotorControl'
import ChatPanel from './ChatPanel'
import LLMStream from './LLMStream'
import ModeButtons from './ModeButtons'
import DrivesPanel from './DrivesPanel'
import PersonDetection from './PersonDetection'
import MemoryPanel from './MemoryPanel'
import LogPanel from './LogPanel'
import RadarScan from './RadarScan'

function Clock() {
  const [t, setT] = useState(new Date())
  useEffect(() => {
    const i = setInterval(() => setT(new Date()), 1000)
    return () => clearInterval(i)
  }, [])
  return <span>{t.toLocaleTimeString('it-IT')}</span>
}

export default function Dashboard() {
  const { mode, ttsSpeak, micState } = useRobotStore()

  const modeLabel = {
    companion_day:   { label: 'Companion',  color: 'text-green-400'  },
    focus_assistant: { label: 'Focus',      color: 'text-cyan-400'   },
    idle_observer:   { label: 'Observer',   color: 'text-purple-400' },
    night_watch:     { label: 'Night Watch',color: 'text-yellow-400' },
  }[mode] ?? { label: mode, color: 'text-slate-400' }

  return (
    <div className="min-h-screen" style={{ background: 'var(--color-bg-base)' }}>

      {/* ── Header ──────────────────────────────────────────────────── */}
      <header style={{ background: '#0a1120', borderBottom: '1px solid #1e293b' }}
              className="sticky top-0 z-50 px-4 py-3">
        <div className="max-w-screen-xl mx-auto flex items-center gap-4">

          {/* Logo */}
          <div className="flex items-center gap-2">
            <div className="status-dot on" />
            <span className="font-bold text-lg tracking-tight text-white">🕷️ Spooky</span>
          </div>

          {/* Mode pill */}
          <span className={`badge border border-current ${modeLabel.color}`}
                style={{ background: 'transparent' }}>
            {modeLabel.label}
          </span>

          {/* Mic / TTS indicators */}
          {micState === 'listening' && (
            <span className="badge" style={{ background: '#dc262620', color: '#f87171', border: '1px solid #f87171' }}>
              🎙️ Listening
            </span>
          )}
          {micState === 'thinking' && (
            <span className="badge" style={{ background: '#7c3aed20', color: '#a78bfa', border: '1px solid #a78bfa' }}>
              🧠 Thinking
            </span>
          )}
          {ttsSpeak && (
            <span className="badge" style={{ background: '#15803d20', color: '#4ade80', border: '1px solid #4ade80' }}>
              🔊 Speaking
            </span>
          )}

          <div className="ml-auto text-sm font-mono" style={{ color: 'var(--color-muted)' }}>
            <Clock />
          </div>
        </div>
      </header>

      {/* ── Main Grid ───────────────────────────────────────────────── */}
      <main className="max-w-screen-xl mx-auto px-4 py-5">
        <div className="grid gap-4" style={{ gridTemplateColumns: '1fr 1fr 320px' }}>

          {/* Col 1 — Robot status */}
          <div className="flex flex-col gap-4">
            <RobotFace />
            <MoodDisplay />
            <SensorPanel />
          </div>

          {/* Col 2 — Camera + controls */}
          <div className="flex flex-col gap-4">
            <CameraFeed />
            <MotorControl />
            <RadarScan />
          </div>

          {/* Col 3 — Sidebar */}
          <div className="flex flex-col gap-4">
            <ModeButtons />
            <DrivesPanel />
            <PersonDetection />
            <MemoryPanel />
          </div>

          {/* Row 2 — Chat + LLM + Logs (full width) */}
          <div className="col-span-3 grid gap-4" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
            <ChatPanel />
            <LLMStream />
            <LogPanel />
          </div>
        </div>
      </main>
    </div>
  )
}
