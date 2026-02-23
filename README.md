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



## API Key

Get a free Gemini API key at [aistudio.google.com](https://aistudio.google.com).

You can provide it two ways:
- **In the UI** — paste it on first load (session only, not stored)
- **As env var** — `GEMINI_API_KEY=your-key streamlit run app.py`
- **In secrets.toml** — for deployment (see above)

---
## Changing the Gemini model
You can also change the Gemini AI model around lines 65, more for it says in the


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

## License
This project is licensed under the MIT License - see the LICENSE file for details