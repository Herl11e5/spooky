# Spooky Dashboard - React + Vite

**Modern React-based dashboard for Spooky companion robot**

## 🚀 Quick Start

### Development
```bash
npm install
npm run dev
```

Dashboard available at: **http://localhost:3000**

The dev server proxies:
- `/api/*` → `http://localhost:5000/api/*`
- `/stream` → `http://localhost:5000/stream` (SSE)
- `/camera` → `http://localhost:5000/camera` (MJPEG)

### Production Build
```bash
npm run build
npm run preview
```

Outputs to `dist/` folder.

## 📦 Stack

- **React 19** - UI framework
- **Vite** - Lightning-fast build tool
- **Tailwind CSS v4** - Utility-first styling
- **Zustand** - Lightweight state management
- **Lucide React** - Beautiful icons
- **Server-Sent Events (SSE)** - Real-time updates

## 🎨 Features

### Components
- **RobotFace** - Animated SVG face with expressions
- **MoodDisplay** - Emotional state + internal drives visualization
- **CameraFeed** - MJPEG stream with scene/object overlay
- **SensorPanel** - Distance, temperature, RAM, pitch/roll sensors
- **MotorControl** - Direction pad + action buttons
- **ChatPanel** - Real-time conversation with quick actions
- **ModeButtons** - Robot mode selector (Companion, Focus, Observer, Night)
- **DrivesPanel** - Energy, social, curiosity, attention, fatigue indicators
- **PersonDetection** - Face recognition results
- **MemoryPanel** - Semantic memory facts
- **LLMStream** - Real-time LLM call monitoring
- **LogPanel** - System activity log
- **RadarScan** - 360° environment map visualization

### Real-time Updates
- **Server-Sent Events (SSE)** for live mode, mood, sensor, voice changes
- **Polling fallback** for state, sensors, vision, facts
- Automatic SSE reconnection on disconnect

## 🔧 Configuration

### API Base URL
Default: `/api` (proxied to `http://localhost:5000`)

Change in `src/services/api.js` if needed.

### Custom Colors
Tailwind color variables in `src/components/*.jsx`:
- `spooky-dark` → `#0d0d0d`
- `spooky-neon-green` → `#39ff14`
- `spooky-neon-cyan` → `#00c8ff`
- `spooky-neon-purple` → `#c77dff`
- `spooky-neon-yellow` → `#ffd60a`
- `spooky-neon-red` → `#ff4444`
- `spooky-neon-pink` → `#ff69b4`

## 🌐 Backend Integration

Flask dashboard.py must provide these endpoints:

### REST APIs
- `GET /api/state` - Robot mode, drives, mic state, transcript
- `GET /api/sensors` - Distance, temperature, RAM, pitch, roll
- `GET /api/vision` - Scene analysis, detected objects, model name
- `GET /api/memory` - Semantic memory facts
- `GET /api/scan` - Last radar scan readings

### Commands
- `POST /api/mode` - Change mode (body: `{mode: "companion_day"}`)
- `POST /api/command` - Send voice command (body: `{command: "hello"}`)
- `POST /api/motor` - Control motors (body: `{action, speed, duration}`)
- `POST /api/scan` - Start environment scan
- `POST /api/summarize` - Summarize memory

### Streams
- `GET /stream` - Server-Sent Events (text/event-stream)
  - Event types: mode, mic, transcript, tts_start, tts_stop, command, scene_analyzed, objects_detected, person, person_lost, heartbeat, obstacle, llm_call, alert, scan_complete
- `GET /camera` - MJPEG stream (image/x-motion-jpeg)

### Events (SSE format)
```json
{type: "mode", mode: "companion_day"}
{type: "mic", state: "listening"}
{type: "transcript", text: "hello spooky"}
{type: "tts_start", text: "Hi there!"}
{type: "heartbeat", dist: 45, temp: 32.5, ram: 880, pitch_deg: 2, roll_deg: -1}
{type: "emotion_expressed", emotion: "happy", duration_ms: 2000}
{type: "llm_call", trigger: "voice_command", prompt: "...", reply: "...", model: "llama", time_ms: 450, fallback: false}
```

## 📜 Store (Zustand)

Global state in `src/store/robotStore.js`:
- `mode` - Current robot mode
- `mood` - Current mood/emotion
- `micState` - Microphone state (idle, listening, thinking)
- `ttsSpeak` - Text-to-speech speaking flag
- `distance, temperature, ramUsage` - Sensor readings
- `pitch, roll` - IMU data
- `scene, objects` - Vision analysis
- `drives` - Internal motivation states
- `facts` - Semantic memories
- `logs, chatHistory, llmCalls` - Event streams

## 🎯 Deployment

### Serve with Flask
1. Build React app: `npm run build`
2. Copy `dist/` to Flask static folder
3. Serve `dist/index.html` for all non-API routes
4. Point `/api`, `/stream`, `/camera` to backend endpoints

### Docker
```dockerfile
FROM node:20 AS builder
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

## 📱 Mobile-Responsive

- Desktop: 3-column layout (left main, right panels)
- Tablet: 2-column layout
- Mobile: Single column, optimized touch controls

## 🐛 Troubleshooting

### CORS Issues
Ensure Flask headers include:
```python
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response
```

### SSE Connection Fails
- Check `/stream` endpoint is serving SSE correctly
- Verify Content-Type is `text/event-stream`
- Check Flask has CORS headers

### Camera Feed Not Loading
- Verify `/camera` MJPEG endpoint returns image data
- Check browser CORS policy if served cross-origin
- Try disabling browser CORS extensions

## 📚 File Structure

```
src/
├── App.jsx               # Root component & initialization
├── index.css             # Tailwind + global styles
├── main.jsx              # React DOM entry point
├── components/           # React components
│   ├── Dashboard.jsx     # Main layout
│   ├── RobotFace.jsx     # Animated SVG face
│   ├── CameraFeed.jsx    # MJPEG viewer
│   ├── SensorPanel.jsx   # Sensor gauges
│   ├── MotorControl.jsx  # Direction pad
│   ├── ChatPanel.jsx     # Chat interface
│   ├── MoodDisplay.jsx   # Mood visualization
│   ├── ModeButtons.jsx   # Mode selector
│   ├── DrivesPanel.jsx   # Drive indicators
│   ├── PersonDetection.jsx
│   ├── MemoryPanel.jsx   # Facts table
│   ├── LLMStream.jsx     # LLM call monitor
│   ├── LogPanel.jsx      # Activity log
│   └── RadarScan.jsx     # 360° radar
├── services/
│   ├── api.js            # API client
│   └── sse.js            # SSE event handler
└── store/
    └── robotStore.js     # Zustand global state
```

## 🎨 Customization

### Change Colors
Edit `tailwind.config.js` to modify the color scheme.

### Add Components
1. Create new file in `src/components/`
2. Use `useRobotStore()` to access state
3. Import and add to `Dashboard.jsx`

### Modify Polling Intervals
In `App.jsx`:
- State poll: `setInterval(..., 5000)` → change 5000ms
- Memory poll: `setInterval(..., 10000)` → change 10000ms

---

**Built with ❤️ for Spooky**
