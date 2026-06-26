# Material Price & Vendor Finder — Cloud Deployment Guide

Host the full app (Gemini AI + Web Scraper + Login + Distributor filter) on Render, free, accessible from any office / any WiFi.

Includes the "Distributors & wholesalers only" mode: blocks marketplaces (IndiaMART, JustDial, TradeIndia, Sulekha, Amazon, Flipkart) and tags each vendor with a distributor-likelihood badge.

---

## What's in this folder

- `app.py` — the full server (login + AI + scraper, all in one)
- `static/index.html` — the web app frontend
- `requirements.txt` — Python libraries Render installs
- `render.yaml` — Render configuration
- `make_users.py` — helper to add/change user logins

---

## Default logins (CHANGE THESE before sharing)

| Username | Password |
|----------|----------|
| mohan | printo123 |
| team1 | welcome123 |
| team2 | welcome123 |

See "Adding users" below to change them.

---

## STEP 1 — Put the code on GitHub

In CMD, inside this folder:

```cmd
cd "path\to\mpvf-cloud"
git init
git add .
git commit -m "cloud app with login"
gh repo create material-vendor-finder --public --source=. --remote=origin --push
```

---

## STEP 2 — Deploy on Render

1. Go to https://render.com and sign up (free, no credit card)
2. Click **New +** → **Web Service**
3. Connect your GitHub and pick the `material-vendor-finder` repo
4. Render auto-detects the settings from `render.yaml`. If asked manually:
   - Runtime: **Python 3**
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
   - Plan: **Free**
5. Before clicking Create, scroll to **Environment Variables** and add:
   - `SERPER_KEY` = your Serper.dev key (the one you already have)
   - `SECRET_KEY` = click "Generate"
   - `LOGIN_SALT` = click "Generate"  ⚠️ see note below
6. Click **Create Web Service**

After ~3 minutes you get a public URL like:
`https://material-vendor-finder.onrender.com`

Share that link + logins with your colleagues. Done!

---

## ⚠️ Important about LOGIN_SALT

If you let Render generate `LOGIN_SALT`, the default passwords above will STOP working
(because the hashes in app.py were made with the salt "mpvf-printo-2026").

**Two options:**

**Option A (easiest):** Set `LOGIN_SALT` = `mpvf-printo-2026` manually in Render
(instead of generating it). Then the default logins work immediately.

**Option B (more secure):** Let Render generate it, then regenerate your user
hashes with that salt and update app.py. Ask if you want help with this.

---

## Adding or changing users

1. Run locally: `python make_users.py`
2. Enter usernames and passwords
3. Copy the printed `USERS = {...}` block into `app.py`
4. Commit and push:
   ```cmd
   git add app.py
   git commit -m "update users"
   git push
   ```
5. Render auto-redeploys in ~2 minutes

---

## Running locally (for testing before deploy)

```cmd
pip install -r requirements.txt
set SERPER_KEY=your-serper-key-here
python app.py
```
Then open http://localhost:5000

---

## Free tier note

Render's free tier "sleeps" after 15 minutes of no use. The first visit after
sleeping takes ~30 seconds to wake up, then it's fast again. For always-on,
Render's paid tier is $7/month — but for occasional office use, free is fine.
