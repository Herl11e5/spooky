const API_BASE = '/api'

export const api = {
  // State
  getState: () => fetch(`${API_BASE}/state`).then(r => r.json()),
  getSensors: () => fetch(`${API_BASE}/sensors`).then(r => r.json()),
  getVision: () => fetch(`${API_BASE}/vision`).then(r => r.json()),
  getMemory: () => fetch(`${API_BASE}/memory`).then(r => r.json()),
  getScan: () => fetch(`${API_BASE}/scan`).then(r => r.json()),
  
  // Commands
  setMode: (mode) => 
    fetch(`${API_BASE}/mode`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    }),
  
  sendCommand: (command) =>
    fetch(`${API_BASE}/command`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command }),
    }),
  
  // Motor control
  motorCmd: (action, speed = 50, duration = 0.8) =>
    fetch(`${API_BASE}/motor`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, speed, duration }),
    }),
  
  // Scan
  startScan: () =>
    fetch(`${API_BASE}/scan`, { method: 'POST' }),
  
  // Summarize
  summarize: () =>
    fetch(`${API_BASE}/summarize`, { method: 'POST' }),
}
