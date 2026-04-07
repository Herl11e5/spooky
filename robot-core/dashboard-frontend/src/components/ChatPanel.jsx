import { useRef, useEffect, useState } from 'react'
import { useRobotStore } from '../store/robotStore'
import { api } from '../services/api'

const QUICK = [
  { label: '👁️ Cosa vedi?',   cmd: 'cosa vedi?' },
  { label: '🧠 Ricordi?',     cmd: 'cosa ricordi?' },
  { label: '👋 Ciao',         cmd: 'ciao' },
  { label: '💾 Riassumi',     cmd: '/summarize' },
]

export default function ChatPanel() {
  const { chatHistory, addChatMessage } = useRobotStore()
  const [input, setInput]   = useState('')
  const [loading, setLoading] = useState(false)
  const boxRef = useRef(null)

  useEffect(() => {
    if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight
  }, [chatHistory])

  const send = async (forcedCmd) => {
    const cmd = (forcedCmd || input).trim()
    if (!cmd) return
    setInput('')
    addChatMessage('user', cmd)
    setLoading(true)
    try { await api.sendCommand(cmd) } catch (e) { console.error(e) }
    finally { setLoading(false) }
  }

  return (
    <div className="card flex flex-col" style={{ height: 340 }}>
      <p className="card-title">💬 Chat</p>

      {/* History */}
      <div ref={boxRef} className="flex-1 overflow-y-auto space-y-2 mb-3 pr-1">
        {chatHistory.length === 0 ? (
          <p className="text-center text-sm py-6" style={{ color: 'var(--color-muted)' }}>
            Nessun messaggio
          </p>
        ) : chatHistory.map((m, i) => (
          <div key={i} className={`text-sm px-3 py-2 rounded-lg ${
            m.role === 'user'
              ? 'ml-6 text-cyan-100'
              : 'mr-6 text-purple-100'
          }`} style={{
            background: m.role === 'user' ? '#0e3a4a' : '#2d1a4a',
            border: `1px solid ${m.role === 'user' ? '#06b6d430' : '#a855f730'}`
          }}>
            <span className="font-semibold mr-1">{m.role === 'user' ? '👤' : '🤖'}</span>
            {m.text}
          </div>
        ))}
        {loading && (
          <p className="text-sm italic mr-6 text-purple-400 px-3">🤖 Sto pensando…</p>
        )}
      </div>

      {/* Quick buttons */}
      <div className="grid grid-cols-2 gap-1 mb-2">
        {QUICK.map(({ label, cmd }) => (
          <button key={cmd} onClick={() => send(cmd)}
                  className="text-xs px-2 py-1 rounded font-medium truncate"
                  style={{ background: '#0e3a4a', color: '#67e8f9', border: '1px solid #06b6d420' }}>
            {label}
          </button>
        ))}
      </div>

      {/* Input */}
      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send()}
          placeholder="Scrivi un comando…"
          className="flex-1 rounded-lg px-3 py-2 text-sm outline-none"
          style={{
            background: '#0f172a',
            border: '1px solid var(--color-border)',
            color: 'var(--color-text)',
          }}
        />
        <button onClick={() => send()} disabled={!input.trim() || loading}
                className="btn btn-cyan px-4">
          ➤
        </button>
      </div>
    </div>
  )
}
