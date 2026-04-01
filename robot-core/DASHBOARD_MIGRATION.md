# Dashboard Integration Guide

## 🔗 Integrating React Dashboard with Flask Backend

### Option A: Serve React Build from Flask (Recommended)

This approach serves the built React app as static files from the same Flask server.

#### Step 1: Build the React App
```bash
cd robot-core/dashboard-frontend
npm run build
```

This creates `dist/` folder with production-ready files.

#### Step 2: Copy to Flask Static Folder
```bash
cp -r dashboard-frontend/dist/* services/static/dashboard/
```

Or move whole folder:
```bash
mkdir -p services/static/dashboard
cp -r dashboard-frontend/dist/* services/static/dashboard/
```

#### Step 3: Update Flask to Serve React
In `services/dashboard.py`, replace the old HTML template with static file serving:

```python
from flask import Flask, send_file, send_from_directory
import os

app = Flask(__name__, static_folder='static/dashboard', static_url_path='/')

# Serve React app index.html for all non-API routes
@app.route('/')
@app.route('/<path:path>')
def serve_react_app(path=''):
    # If file exists in static folder, serve it
    if path and os.path.exists(os.path.join('static/dashboard', path)):
        return send_from_directory('static/dashboard', path)
    # Otherwise serve index.html (let React Router handle routing)
    return send_from_directory('static/dashboard', 'index.html')

# Keep all API endpoints unchanged!
@app.route('/api/state')
def get_state():
    # ... existing code ...

@app.route('/api/sensors')
def get_sensors():
    # ... existing code ...

# etc...
```

#### Step 4: No Proxy Needed!
Update `vite.config.js` to point directly to Flask:

```javascript
server: {
  proxy: {
    '/api': {
      target: 'http://localhost:5000',
      changeOrigin: true,
    },
    '/stream': {
      target: 'http://localhost:5000',
      changeOrigin: true,
      ws: true,
    },
    '/camera': {
      target: 'http://localhost:5000',
      changeOrigin: true,
    },
  },
},
```

---

### Option B: Run React on Separate Port

Run React dev server on port 3000, Flask on 5000. Keep them separate.

#### Step 1: Start Flask Backend
```bash
cd robot-core
python main.py  # Runs on http://localhost:5000
```

#### Step 2: Start React Dev Server
```bash
cd robot-core/dashboard-frontend
npm run dev    # Runs on http://localhost:3000
```

#### Step 3: Access
- React Dashboard: http://localhost:3000
- Flask API: http://localhost:5000/api/...
- Camera: http://localhost:5000/camera
- SSE Stream: http://localhost:5000/stream

The dev server's proxy (vite.config.js) will forward API calls to Flask.

**Pros**: Easy development workflow
**Cons**: Two ports, two servers to manage, cross-origin concerns

---

### Option C: Docker Compose (Production)

#### `docker-compose.yml`
```yaml
version: '3.8'

services:
  spooky-backend:
    build: ./robot-core
    ports:
      - "5000:5000"
    environment:
      - FLASK_ENV=production
    volumes:
      - ./robot-core/config:/app/config
      - ./robot-core/data:/app/data

  spooky-dashboard:
    build: ./robot-core/dashboard-frontend
    ports:
      - "3000:3000"
    depends_on:
      - spooky-backend
    environment:
      - VITE_API_URL=http://localhost:5000

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
    depends_on:
      - spooky-backend
      - spooky-dashboard
```

---

## 📝 Backend Checklist

Ensure your Flask backend has these endpoints:

### Required Endpoints

- [ ] `GET /api/state` → `{mode, drives, mic_state, last_transcript, tts_speaking}`
- [ ] `GET /api/sensors` → `{dist, temp, ram, pitch, roll}`
- [ ] `GET /api/vision` → `{scene, objects, model}`
- [ ] `GET /api/memory` → `{facts: [{key, value, confidence}, ...]}`
- [ ] `GET /api/scan` → `{readings: [{dist, angle}, ...]}`
- [ ] `POST /api/mode` (body: `{mode}`) → Change mode
- [ ] `POST /api/command` (body: `{command}`) → Send command
- [ ] `POST /api/motor` (body: `{action, speed, duration}`) → Motor control
- [ ] `POST /api/scan` → Start radar scan
- [ ] `POST /api/summarize` → Summarize memory
- [ ] `GET /stream` → SSE event stream
- [ ] `GET /camera` → MJPEG stream

### SSE Event Types

Your `/stream` endpoint should emit:

```
{type: "mode", mode: "..."}
{type: "mic", state: "listening|thinking|idle"}
{type: "transcript", text: "..."}
{type: "tts_start", text: "..."}
{type: "tts_stop"}
{type: "command", text: "..."}
{type: "scene_analyzed", text: "..."}
{type: "objects_detected", objects: "..."}
{type: "person", name: "...", conf: 0.9, known: true}
{type: "person_lost"}
{type: "heartbeat", dist: 100, temp: 32, ram: 800, pitch_deg: 2, roll_deg: -1}
{type: "llm_call", trigger: "...", prompt: "...", reply: "...", model: "...", time_ms: 450, fallback: false}
{type: "alert", level: 2, reason: "..."}
{type: "scan_complete", readings: [...]}
```

---

## 🚀 Deployment Steps

### 1. Build React
```bash
cd dashboard-frontend
npm run build
```

### 2. Copy to Flask Static
```bash
mkdir -p services/static/dashboard
cp -r dashboard-frontend/dist/* services/static/dashboard/
```

### 3. Update Flask Dashboard
Replace old dashboard HTML with static file serving (see Option A above).

### 4. Test
```bash
cd robot-core
python main.py
# Visit http://localhost:5000/
```

### 5. Commit
```bash
git add -A
git commit -m "chore: Deploy new React dashboard"
git push
```

---

## 🔄 Development Workflow

### First Time Setup
```bash
# Install dependencies
cd dashboard-frontend
npm install

# Build for testing
npm run build
npm run preview  # Local production preview
```

### Active Development
```bash
# Terminal 1: Backend
cd robot-core
python main.py  # Runs on 5000

# Terminal 2: Frontend
cd dashboard-frontend
npm run dev  # Runs on 3000 with hot-reload
```

### Before Committing
```bash
# Build and test
npm run build
npm run preview

# If everything works, commit both backend AND frontend changes
git add -A
git commit -m "feat: Update dashboard"
git push
```

---

## 🎯 Key Files to Track

- `dashboard-frontend/` - React app source
- `dashboard-frontend/dist/` - Production build (generated, can .gitignore)
- `services/dashboard.py` - Flask backend (no longer has HTML template!)
- `services/static/dashboard/` - Where React build gets deployed (can .gitignore)

---

## ✅ Verification

After deployment, verify all endpoints work:

```bash
# Check API endpoints
curl http://localhost:5000/api/state
curl http://localhost:5000/api/sensors
curl http://localhost:5000/api/vision

# Check SSE stream
curl -N http://localhost:5000/stream

# Check camera feed
curl -I http://localhost:5000/camera

# Check React app
curl http://localhost:5000/  # Should return index.html
```

---

## 🐛 Troubleshooting

### "Cannot GET /"
- Flask is not serving `static/dashboard/index.html`
- Make sure you copied the React build to the static folder
- Verify Flask route is set up correctly

### API calls return 404
- Flask endpoints don't exist or have different URL paths
- Check CORS headers are being sent
- Look at Flask error logs for details

### SSE connection fails
- `/stream` endpoint not returning `text/event-stream` Content-Type
- Check Flask is sending proper SSE headers
- Browser console should show connection errors

### Images/CSS not loading
- Static files path incorrect in Flask
- Check `services/static/dashboard/` folder contains built files
- Verify Flask is serving static files correctly

