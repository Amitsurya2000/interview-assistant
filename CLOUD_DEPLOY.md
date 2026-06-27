# Cloud Deployment Guide

## Repo
https://github.com/Amitsurya2000/interview-assistant

---

## Deploy on Render (1-Click)

1. Go to https://dashboard.render.com
2. Click **New +** → **Blueprint**
3. Connect repo `Amitsurya2000/interview-assistant`
4. Render reads `render.yaml` and creates 2 services:
   - `interview-assistant` (Whisper STT server)
   - `ollama` (LLM backend)
5. Set `AUTH_TOKEN` as environment variable on the web service
6. Deploy

---

## Deploy on Railway

1. Go to https://railway.app
2. **New Project** → **Deploy from GitHub repo**
3. Select `Amitsurya2000/interview-assistant`
4. Add environment variables:
   - `AUTH_TOKEN` = your-secret-token
   - `OLLAMA_BASE_URL` = http://ollama:11434
5. Add a second service: **New** → **Add Dockerfile** → point at `Dockerfile.ollama`
6. Deploy both

---

## Run Server Locally (without Docker)

```bash
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
.\venv\Scripts\python server.py
```

---

## Connect from Windows

### Audio Client
```powershell
.\venv\Scripts\python audio_client.py --ws "ws://YOUR_SERVER_URL:8123/" --token "your-token" --device 0
```

### Overlay
Open in browser:
```
overlay.html?ws=ws://YOUR_SERVER_URL:8123/&token=your-token
```
