import { useState } from 'react'
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

export default function Dashboard() {
  const [layout, setLayout] = useState('desktop') // desktop, mobile

  return (
    <div className="min-h-screen bg-spooky-dark text-white">
      {/* Header */}
      <header className="border-b border-spooky-neon-green/20 bg-black/50 backdrop-blur-sm">
        <div className="container mx-auto px-4 py-4 flex justify-between items-center">
          <div className="flex items-center gap-3">
            <div className="w-3 h-3 bg-spooky-neon-green rounded-full animate-pulse-neon" />
            <h1 className="text-2xl font-bold text-spooky-neon-green">🤖 Spooky Dashboard</h1>
          </div>
          <div className="text-sm text-spooky-neon-cyan">
            {new Date().toLocaleTimeString('it')}
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="container mx-auto px-4 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 auto-rows-max">
          {/* Left Column - Main Controls & Camera */}
          <div className="lg:col-span-2 space-y-6">
            {/* Robot Face + Mood */}
            <div className="grid grid-cols-2 gap-4">
              <RobotFace />
              <MoodDisplay />
            </div>

            {/* Camera Feed */}
            <CameraFeed />

            {/* Sensors Grid */}
            <SensorPanel />

            {/* Radar Scan */}
            <RadarScan />

            {/* Motor Control */}
            <MotorControl />

            {/* Chat */}
            <ChatPanel />
          </div>

          {/* Right Column - Info & Monitoring */}
          <div className="space-y-6">
            {/* Mode Selector */}
            <ModeButtons />

            {/* Drives */}
            <DrivesPanel />

            {/* Person Detection */}
            <PersonDetection />

            {/* Memory Facts */}
            <MemoryPanel />

            {/* LLM Stream */}
            <LLMStream />

            {/* Logs */}
            <LogPanel />
          </div>
        </div>
      </main>
    </div>
  )
}
