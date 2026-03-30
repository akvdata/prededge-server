# PredEdge Server — Deployment Guide

## What's inside

```
prededge-server/
├── server.py           ← FastAPI backend (real stock data via yfinance)
├── index.html          ← Dashboard (served by FastAPI)
├── requirements.txt    ← Python dependencies
├── Procfile            ← Railway/Render entrypoint
└── portfolio.json      ← Created automatically (portfolio data)
```

## Option A: Run Locally (test first)

```bash
cd prededge-server
pip install -r requirements.txt
python server.py
```

Open http://localhost:8000 — you'll see the live dashboard with real stock prices.

## Option B: Deploy to Railway ($5/month, simplest)

### 1. Create a GitHub repo

```bash
cd prededge-server
git init
git add .
git commit -m "PredEdge Market Intelligence"
```

Go to https://github.com/new and create a repo called `prededge-server`.

```bash
git remote add origin https://github.com/YOUR-USERNAME/prededge-server.git
git branch -M main
git push -u origin main
```

### 2. Deploy on Railway

1. Go to https://railway.app and sign in with GitHub
2. Click **"New Project"** → **"Deploy from GitHub Repo"**
3. Select your `prededge-server` repo
4. Railway auto-detects Python and deploys
5. Click **"Generate Domain"** in Settings to get your public URL
6. Your dashboard is now live at `https://your-app.up.railway.app`

That's it. Railway reads the Procfile, installs requirements.txt, and runs uvicorn.

### 3. Every time you update

```bash
git add .
git commit -m "update"
git push
```

Railway auto-deploys on every push.

## Option C: Deploy to Render (free tier available)

1. Go to https://render.com and sign in with GitHub
2. Click **"New" → "Web Service"**
3. Connect your `prededge-server` repo
4. Settings:
   - Runtime: Python
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn server:app --host 0.0.0.0 --port $PORT`
5. Click Deploy

Free tier: service sleeps after 15 min inactivity, wakes on next request (~30s cold start).

## Security Notes

- This dashboard is **read-only** — it fetches prices and displays them, no trading
- No API keys or private keys are stored on the server
- Portfolio data is saved in a local JSON file on the server
- Your trading engine (with Polymarket private keys) stays on your ThinkPad
