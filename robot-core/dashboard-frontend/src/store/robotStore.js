import { create } from 'zustand'

export const useRobotStore = create((set) => ({
  // Robot state
  mode: 'companion_day',
  mood: 'content',
  micState: 'idle',
  ttsSpeak: false,
  lastTranscript: '',

  // Sensors & Observation
  distance: 999,
  temperature: 0,
  ramUsage: 0,
  pitch: 0,
  roll: 0,
  
  // Vision
  scene: '',
  objects: '',
  detectedPerson: null,
  
  // Drives
  drives: {
    energy: 0.5,
    social_drive: 0.5,
    curiosity: 0.5,
    attention: 0.5,
    interaction_fatigue: 0,
  },
  
  // Memory
  facts: [],
  
  // Logs & Messages
  logs: [],
  chatHistory: [],
  llmCalls: [],
  
  // Commands
  setMode: (mode) => set({ mode }),
  setMood: (mood) => set({ mood }),
  setMicState: (micState) => set({ micState }),
  setTtsSpeaking: (speaking) => set({ ttsSpeak: speaking }),
  setTranscript: (transcript) => set({ lastTranscript: transcript }),
  
  setSensors: (sensors) => set({
    distance: sensors.dist ?? 999,
    temperature: sensors.temp ?? 0,
    ramUsage: sensors.ram ?? 0,
    pitch: sensors.pitch ?? 0,
    roll: sensors.roll ?? 0,
  }),
  
  setVision: (vision) => set({
    scene: vision.scene || '',
    objects: vision.objects || '',
  }),
  
  setDrives: (drives) => set({ drives }),
  
  setDetectedPerson: (person) => set({ detectedPerson: person }),
  
  setFacts: (facts) => set({ facts }),
  
  addLog: (log) => set((state) => ({
    logs: [log, ...state.logs].slice(0, 100),
  })),
  
  addChatMessage: (role, text) => set((state) => ({
    chatHistory: [...state.chatHistory, { role, text, timestamp: Date.now() }].slice(-50),
  })),
  
  addLlmCall: (call) => set((state) => ({
    llmCalls: [
      { ...call, timestamp: Date.now() },
      ...state.llmCalls,
    ].slice(0, 30),
  })),
}))
