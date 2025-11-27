# AgroPulse – Market-Timed Crop Intelligence

AgroPulse is a Flask web experience that helps growers decide the best sowing window by combining:

- landing and authentication flow (register/login/logout) with a neon-inspired UI
- dashboard where growers enter crop + land size (optional city override)
- automatic browser geolocation with fallback to Bengaluru if permission is denied/ignored for >5 sec
- OpenWeather injection so AI understands local conditions
- Google Gemini 2.5 Flash prompts tailored to return sectioned guidance (market timing, weather checklist, demand outlook, timeline, action items)
- expandable history so users can revisit the last five playbooks

---

## Stack & Dependencies

| Layer | Details |
| ----- | ------- |
| Backend | Flask 3, Python 3.13 |
| Frontend | Jinja2 templates + custom CSS (Space Grotesk, neon gradients) |
| AI | Google Gemini 2.5 Flash via Generative Language API |
| Weather | OpenWeather “Current weather” REST endpoint |
| Markdown rendering | `markdown2` (server converts Gemini Markdown to HTML) |

### Key Python deps (`requirements.txt`)
```
Flask==3.0.3
requests==2.32.3
markdown2==2.5.1
python-dotenv==1.0.0
```

---

## Local Setup

```bash
git clone <repo>
cd AI-Crop
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

**Configure environment variables:**

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and add your API keys:
   ```bash
   # Required
   GEMINI_API_KEY=your-gemini-api-key-here
   OPEN_WEATHER_API_KEY=your-openweather-api-key-here
   
   # Optional
   FLASK_SECRET_KEY=your-secret-key-here
   DEFAULT_LAT=12.9716
   DEFAULT_LON=77.5946
   DEFAULT_CITY=Bengaluru
   PORT=5321
   ```

   **Get your API keys:**
   - **Gemini API**: Visit [Google AI Studio](https://makersuite.google.com/app/apikey) to generate a key
   - **OpenWeather API**: Sign up at [OpenWeatherMap](https://openweathermap.org/api) for a free API key

3. Run the app:
   ```bash
   flask --app app run --debug
   # or: python app.py
   ```

**Note:** The `.env` file is gitignored and will not be committed to the repository.

Visit `http://127.0.0.1:5000/`.

---

## Application Walkthrough

### Routes (`app.py`)
| Route | Method | Description |
| ----- | ------ | ----------- |
| `/` | GET | Landing hero with CTAs |
| `/register` | GET/POST | Creates in-memory user; fields: farm name, username, password |
| `/login` | GET/POST | Authenticates against in-memory store |
| `/logout` | GET | Clears session |
| `/dashboard` | GET/POST | Protected area for request form, weather widget, AI output, history |
| `/api/weather` | GET | Auth-protected JSON endpoint; queries OpenWeather using supplied lat/lon |
| `/api/ping` | GET | Health check |

### Dashboard Flow
1. **Location modal** prompts for geolocation.  
   - Uses `navigator.permissions` to detect status.  
   - If no response within 5 sec, auto-falls back to Bengaluru coordinates (configurable).  
   - Weather widget immediately displays Bengaluru data so UI never shows “fetching…”.
2. **Request form** requires crop + land size, optional city override.  
   - Hidden fields carry lat/lon captured from geolocation or fallback.
3. **Server POST** (`dashboard` route) steps:
   - Validates required fields.
   - Applies fallback coordinates if empty.
   - Calls `fetch_weather(lat, lon)` to hit OpenWeather (metric units).  
   - Chooses `location_name` from user city override → weather city → fallback city → raw coordinates.
   - Constructs Gemini prompt (sectioned Markdown).  
   - Calls Generative Language API endpoint and converts Markdown to HTML via `markdown2`.
   - Stores result (crop, land size, location, weather snapshot, HTML, timestamp) in an in-memory history list per user (latest first, trimmed to 5).
4. **Template rendering**:
   - Weather widget shows latest weather snapshot (or fallback stub).  
   - Result card renders sanitized HTML `{{ active_result.insights_html|safe }}`.  
   - History uses `<details>` toggles to inspect previous runs without leaving the page.

### Gemini Prompting
- Model: `gemini-2.5-flash`.
- Endpoint: `POST https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent`.
- Prompt enforces the following Markdown sections:
  1. Market-Timed Sowing Window
  2. Weather & Soil Checklist
  3. Demand Outlook & Alternatives
  4. Care-to-Harvest Timeline
  5. Action Notes
- Includes current weather metrics in the prompt for context.

### Weather Data
- Uses OpenWeather’s current weather endpoint.  
- Inputs: `lat`, `lon`, `appid`, `units=metric`.  
- Returned attributes stored: temperature, humidity, conditions, wind, city name.
- If API fails, UI falls back to placeholders; AI prompt receives “Weather data unavailable.”

---

## UI / UX Details

- `templates/base.html`: global shell with sticky header, nav CTAs, flash alerts.
- `templates/landing.html`: hero + value props.
- `templates/register.html` / `templates/login.html`: glassmorphism auth cards.
- `templates/dashboard.html`:  
  - hero metrics, weather widget, location modal, request form, result card, collapsible history.  
  - JavaScript handles geolocation, weather refresh, fallback timers.

- `static/styles.css`:  
  - CSS variables for neon palette, responsive grids, modal styling, Markdown output formatting.  
  - Weather widget, buttons, `details/summary` history interactions.

---

## Extending the Project

- **Persistence**: replace in-memory `users` / `user_histories` with a database (PostgreSQL, Firebase, etc.).
- **Auth security**: hash passwords, add rate limiting, CSRF protection.
- **Real market data**: integrate commodity price APIs to further inform Gemini prompts.
- **Background jobs**: schedule refreshes for weather or market signals.
- **Deployment**: containerize via Docker, front with Gunicorn + Nginx, add HTTPS and secret management.

---

## Troubleshooting

- **Geolocation blocked**: UI falls back to Bengaluru automatically; inform users to enable permissions for hyper-local insights.
- **OpenWeather errors**: verify `OPEN_WEATHER_API_KEY` and network access; placeholders will appear until data returns.
- **Gemini quota issues**: ensure `GEMINI_API_KEY` is valid; server returns a message like “Gemini API error: ...”.
- **Markdown artifacts**: Gemini is instructed not to overuse formatting; `markdown2` ensures clean HTML rendering.

---

## License

This demo is unlicensed / proprietary unless otherwise noted. Adapt and secure before production use. Whichever deployment path you choose, ensure API keys are stored safely and rotated regularly.

