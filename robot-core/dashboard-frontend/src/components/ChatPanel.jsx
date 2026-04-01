import { useRef, useEffect, useState } from 'react'
import { useRobotStore } from '../store/robotStore'
import { api } from '../services/api'
import { Send, MessageCircle } from 'lucide-react'

export default function ChatPanel() {
  const { chatHistory, addChatMessage } = useRobotStore()
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const chatBoxRef = useRef(null)

  useEffect(() => {
    if (chatBoxRef.current) {
      chatBoxRef.current.scrollTop = chatBoxRef.current.scrollHeight
    }
  }, [chatHistory])

  const handleSend = async (forcedCmd) => {
    const cmd = (forcedCmd || input).trim()
    if (!cmd) return

    setInput('')
    addChatMessage('user', cmd)
    setLoading(true)

    try {
      await api.sendCommand(cmd)
    } catch (err) {
      console.error('Command error:', err)
    } finally {
      setLoading(false)
    }
  }

  const quickCommands = [
    { label: '👁️ Scene', cmd: 'cosa vedi?' },
    { label: '🧠 Memory', cmd: 'cosa ricordi?' },
    { label: '👋 Hello', cmd: 'ciao' },
    { label: '💾 Summarize', cmd: '/summarize' },
  ]

  return (
    <div className="border-2 border-spooky-neon-purple rounded-lg p-4 bg-black/30 h-96 flex flex-col">
      <h3 className="text-lg font-bold text-spooky-neon-purple flex items-center gap-2 mb-3">
        <MessageCircle className="w-5 h-5" />
        Chat
      </h3>

      {/* Chat History */}
      <div
        ref={chatBoxRef}
        className="flex-1 overflow-y-auto mb-3 space-y-2"
      >
        {chatHistory.length === 0 ? (
          <div className="text-spooky-neon-cyan/50 text-sm text-center py-8">
            No messages yet...
          </div>
        ) : (
          chatHistory.map((msg, idx) => (
            <div
              key={idx}
              className={`text-sm p-2 rounded ${
                msg.role === 'user'
                  ? 'bg-spooky-neon-cyan/20 text-spooky-neon-cyan ml-4'
                  : 'bg-spooky-neon-purple/20 text-spooky-neon-purple mr-4'
              }`}
            >
              <span className="font-bold">{msg.role === 'user' ? '👤' : '🤖'} </span>
              {msg.text}
            </div>
          ))
        )}
        {loading && (
          <div className="text-spooky-neon-purple/60 text-sm italic">
            🤖 Thinking...
          </div>
        )}
      </div>

      {/* Quick Actions */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        {quickCommands.map(({ label, cmd }) => (
          <button
            key={cmd}
            onClick={() => handleSend(cmd)}
            className="text-xs px-2 py-1 bg-spooky-neon-cyan/20 text-spooky-neon-cyan rounded hover:bg-spooky-neon-cyan/40 truncate"
          >
            {label}
          </button>
        ))}
      </div>

      {/* Input */}
      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSend()}
          placeholder="Type a command..."
          className="flex-1 bg-black/50 text-spooky-neon-cyan border border-spooky-neon-cyan/30 rounded px-3 py-2 text-sm placeholder-spooky-neon-cyan/50 focus:outline-none focus:border-spooky-neon-cyan"
        />
        <button
          onClick={handleSend}
          disabled={!input.trim() || loading}
          className="bg-spooky-neon-purple text-white px-3 py-2 rounded hover:opacity-80 disabled:opacity-50"
        >
          <Send className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}
