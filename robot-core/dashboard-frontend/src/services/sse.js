import { useRobotStore } from '../store/robotStore'

export const initSSE = () => {
  const es = new EventSource('/stream')
  
  es.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      const store = useRobotStore.getState()
      
      // Handle different event types
      switch (data.type) {
        case 'mode':
          store.setMode(data.mode)
          break
          
        case 'mic':
          store.setMicState(data.state)
          break
          
        case 'transcript':
          store.setTranscript(data.text)
          break
          
        case 'tts_start':
          store.addChatMessage('spooky', data.text)
          store.setTtsSpeaking(true)
          break
          
        case 'tts_stop':
          store.setTtsSpeaking(false)
          break
          
        case 'command':
          store.addChatMessage('user', data.text)
          break
          
        case 'scene_analyzed':
          store.setVision({ scene: data.text || data.description || '' })
          break
          
        case 'objects_detected':
          store.setVision({ objects: data.text || data.objects || '' })
          break
          
        case 'person':
          store.setDetectedPerson({
            name: data.name || 'Sconosciuto',
            confidence: data.conf || 0,
            known: data.known || false,
          })
          break
          
        case 'person_lost':
          store.setDetectedPerson(null)
          break
          
        case 'heartbeat':
          store.setSensors({
            dist: data.dist || 999,
            temp: data.temp || 0,
            ram: data.ram || 0,
            pitch: data.pitch_deg || 0,
            roll: data.roll_deg || 0,
          })
          break
          
        case 'llm_call':
          store.addLlmCall({
            model: data.model,
            trigger: data.trigger,
            prompt: data.prompt,
            reply: data.reply,
            time_ms: data.time_ms,
            fallback: data.fallback,
            context: data.context,
          })
          break
          
        case 'alert':
          store.addLog(`[ALERT L${data.level}] ${data.reason}`)
          break
          
        default:
          if (data.type !== 'ping') {
            const msg = `[${data.type}] ${JSON.stringify(data).replace(/{"type":"[^"]+",?/, '').slice(0, 60)}`
            store.addLog(msg)
          }
      }
    } catch (err) {
      console.error('SSE parse error:', err)
    }
  }
  
  es.onerror = (err) => {
    console.warn('SSE disconnected:', err)
    // Try to reconnect after 3 seconds
    setTimeout(initSSE, 3000)
  }
  
  return es
}
