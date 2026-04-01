import { useEffect } from 'react'
import { useRobotStore } from './store/robotStore'
import { initSSE } from './services/sse'
import { api } from './services/api'
import Dashboard from './components/Dashboard'

function App() {
  const store = useRobotStore()

  useEffect(() => {
    // Initialize SSE connection
    initSSE()

    // Fetch initial state
    const fetchInitialState = async () => {
      try {
        const [state, sensors, vision, memory] = await Promise.all([
          api.getState(),
          api.getSensors(),
          api.getVision(),
          api.getMemory(),
        ])

        store.setMode(state.mode || 'companion_day')
        store.setSensors(sensors)
        store.setVision(vision)
        store.setFacts(memory.facts || [])
        store.setDrives(state.drives || {})
      } catch (err) {
        console.error('Failed to fetch initial state:', err)
      }
    }

    fetchInitialState()

    // Poll for state every 5 seconds
    const stateInterval = setInterval(async () => {
      try {
        const state = await api.getState()
        store.setMode(state.mode || 'companion_day')
        store.setDrives(state.drives || {})
      } catch (err) {
        console.error('State poll error:', err)
      }
    }, 5000)

    // Poll for facts every 10 seconds
    const memoryInterval = setInterval(async () => {
      try {
        const memory = await api.getMemory()
        store.setFacts(memory.facts || [])
      } catch (err) {
        console.error('Memory poll error:', err)
      }
    }, 10000)

    return () => {
      clearInterval(stateInterval)
      clearInterval(memoryInterval)
    }
  }, [store])

  return <Dashboard />
}

export default App
