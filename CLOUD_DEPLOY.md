# Cloud Deployment Guide

## Architecture

```
Your Windows PC (audio_client.py + overlay.html)
       │
       │ WebSocket (secure, authenticated)
       ▼
Cloud VM / VPS (server.py + Whisper + Ollama)
```

**Server (cloud):** Whisper STT + Ollama LLM
**Client (local):** Audio capture + browser overlay

---

## Option 1: VPS with Docker (Recommended)

### Prerequisites
- A Linux VPS (2GB+ RAM, 4GB+ recommended)
- Docker & Docker Compose installed
- GPU optional but recommended for Whisper

### Deploy

```bash
# Clone on your VPS
git clone <your-repo> interview-assistant
cd interview-assistant

# Set auth token
export AUTH_TOKEN="your-secret-token-here"

# Start
docker compose up -d

# Pull the LLM model
docker compose exec ollama ollama pull llama3.2

# Check logs
docker compose logs -f server
```

### Connect from Windows
```powershell
# Start audio client:
$env:AUTH_TOKEN="your-secret-token-here"
.\venv\Scripts\python audio_client.py --ws "ws://YOUR_VPS_IP:8123/" --device <device_id>

# Open overlay in browser:
# overlay.html?ws=ws://YOUR_VPS_IP:8123/&token=your-secret-token-here
```

---

## Option 2: Render (WebSocket support)

1. Create a `render.yaml`:

```yaml
services:
  - name: interview-assistant
    type: web
    env: docker
    dockerfilePath: ./Dockerfile
    envVars:
      - key: AUTH_TOKEN
        value: your-secret-token
      - key: OLLAMA_BASE_URL
        value: http://your-ollama-host:11434
```

2. Deploy via Render dashboard or CLI.

> Note: Render doesn't natively support Docker Compose. You need an external Ollama host or use Render's NVMe SSD instances.

---

## Option 3: Railway

1. Push to a GitHub repo
2. Create `railway.json` or use the dashboard
3. Add `AUTH_TOKEN` and `OLLAMA_BASE_URL` as environment variables
4. Deploy

---

## Option 4: Google Cloud Run

```bash
gcloud run deploy interview-assistant \
  --source . \
  --set-env-vars "AUTH_TOKEN=your-token,OLLAMA_BASE_URL=http://ollama-service:11434" \
  --allow-unauthenticated \
  --memory 4Gi
```

---

## Connecting Your Local Components

### 1. Audio Client
```powershell
.\venv\Scripts\python audio_client.py --ws "ws://YOUR_SERVER_IP:8123/" --token "your-token" --device <id>
```

### 2. Overlay
Open in browser:
```
overlay.html?ws=ws://YOUR_SERVER_IP:8123/&token=your-token
```

### 3. Environment Variables
Set these on Windows to avoid passing them every time:
```powershell
$env:WS_URL="ws://YOUR_SERVER_IP:8123/"
$env:AUTH_TOKEN="your-token"
```

---

## Security Notes

- Always set `AUTH_TOKEN` in production
- Use `wss://` (WebSocket Secure) if your server supports SSL
- For production, consider a reverse proxy (Caddy, Nginx) with TLS termination
- The server binds to `0.0.0.0` - the auth token is the only protection
