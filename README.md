# 🧭 GoWhere

**Turn your vibe into a destination.** An AI-powered spatial discovery engine for the indecisive adventurer.

---

## Features

- **📍 POI Radar** — Enter any city, get 5 non-obvious local gems with an interactive map
- **✨ Vibe Engine** — Describe a mood in plain language, get 3 matched destinations plotted on a map
- **🗺️ Adventure History** — All sessions stored locally in SQLite
- **📱 Mobile-first** — Works great on your phone while you're out

---

## File Structure

```
gowhere/
├── app.py              ← Main Streamlit app (all logic)
├── style.css           ← Custom dark UI styling
├── requirements.txt    ← Python dependencies
├── .gitignore
└── .streamlit/
    ├── config.toml     ← Theme + server config
    └── secrets.toml    ← API key (DO NOT commit)
```

---

## Local Setup (2 minutes)

```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/gowhere.git
cd gowhere

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run
streamlit run app.py
```

Open `http://localhost:8501` — enter your Gemini API key in the UI.

---

## 🌐 Deploy (so you can use it on your phone anywhere)

### Option 1: Streamlit Community Cloud (FREE — Recommended)

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your GitHub repo
4. In **Advanced settings → Secrets**, add:
   ```toml
   GEMINI_API_KEY = "your-api-key-here"
   ```
5. Deploy → get a URL like `https://gowhere.streamlit.app`
6. **Bookmark it on your phone** ✅

> Note: SQLite history won't persist on Streamlit Cloud (filesystem resets). History works locally only.

### Option 2: Railway (FREE tier, persistent)

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```
Set `GEMINI_API_KEY` in Railway's environment variables.

### Option 3: ngrok (quick local tunnel — your laptop must be on)

```bash
pip install pyngrok
ngrok http 8501
```
Share the `https://xxxx.ngrok.io` URL with friends.

---

## API Key

Get a free Gemini API key at [aistudio.google.com](https://aistudio.google.com).

You can provide it two ways:
- **In the UI** — paste it on first load (session only, not stored)
- **As env var** — `GEMINI_API_KEY=your-key streamlit run app.py`
- **In secrets.toml** — for deployment (see above)

---

## Extending GoWhere

The codebase is structured for community connectors:

| What | Where |
|---|---|
| Change AI model | `get_gemini()` in `app.py` |
| Add weather context | Inject into `VIBE_PROMPT` |
| Add opening hours | Extend POI JSON schema in `POI_PROMPT` |
| Add transit routing | Replace PolyLine with OSRM/Google Directions |
| Add event data | Pull from Eventbrite/Ticketmaster API |

---

Made with Streamlit + Gemini + Folium