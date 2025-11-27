import json
import os
import re
import secrets
from datetime import datetime
from functools import wraps

import markdown2
import requests
from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(16))

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
OPEN_WEATHER_API_KEY = os.environ.get("OPEN_WEATHER_API_KEY")
DEFAULT_LAT = os.environ.get("DEFAULT_LAT", "12.9716")
DEFAULT_LON = os.environ.get("DEFAULT_LON", "77.5946")
DEFAULT_CITY = os.environ.get("DEFAULT_CITY", "Bengaluru")

# Validate required API keys
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY is required. Please set it in your .env file.")
if not OPEN_WEATHER_API_KEY:
    raise ValueError("OPEN_WEATHER_API_KEY is required. Please set it in your .env file.")

# In-memory user store & insights cache (demo purposes only).
users = {}
user_histories = {}


def login_required(view_func):
    """Basic session gatekeeper for non-production use."""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "username" not in session:
            flash("Please log in first ✨", "warning")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped


def fetch_weather(lat, lon):
    """Get current weather snapshot for dashboard + prompt conditioning."""
    if not lat or not lon or not OPEN_WEATHER_API_KEY:
        return {}

    try:
        response = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={
                "lat": lat,
                "lon": lon,
                "units": "metric",
                "appid": OPEN_WEATHER_API_KEY,
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        main = data.get("main", {})
        weather = data.get("weather", [{}])[0]
        wind = data.get("wind", {})
        return {
            "city": data.get("name") or "",
            "temp_c": main.get("temp"),
            "humidity": main.get("humidity"),
            "conditions": weather.get("description", "").title(),
            "icon": weather.get("icon"),
            "wind": wind.get("speed"),
            "lat": lat,
            "lon": lon,
        }
    except requests.RequestException:
        return {}


def generate_crop_plan(crop_name, land_size, location_name, weather_snapshot):
    """Call Gemini to craft a tailored crop timing plan with structured data."""
    base_url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    weather_line = (
        f"Weather now in {location_name}: "
        f"{weather_snapshot.get('temp_c', 'N/A')}°C, "
        f"humidity {weather_snapshot.get('humidity', 'N/A')}%, "
        f"conditions {weather_snapshot.get('conditions', 'N/A')}."
        if weather_snapshot
        else "Weather data unavailable."
    )
    prompt = f"""
    You are AgroPulse, an elite agronomy strategist. Given:
    - Crop: {crop_name}
    - Land size (acres or hectares): {land_size}
    - Location: {location_name}
    - {weather_line}

    You MUST respond with ONLY valid JSON (no markdown code blocks, no explanations, no trailing text). The JSON structure must be:
    {{
      "summary": {{
        "optimal_planting_date": "May 15, 2026",
        "expected_harvest_date": "Aug 23, 2026",
        "expected_market_price_inr": "₹1,04,000 per ton",
        "irrigation_method": "Drip irrigation",
        "watering_frequency": "Every 3-4 days"
      }},
      "sections": {{
        "market_timed": "## Market-Timed Sowing Window\\nYour detailed explanation here...",
        "weather_soil": "## Weather & Soil Checklist\\n- Point 1\\n- Point 2",
        "demand_outlook": "## Demand Outlook & Alternatives\\nYour analysis here...",
        "timeline": "## Care-to-Harvest Timeline\\n- **Date:** Task description",
        "actions": "## Action Notes\\n1. Action item 1\\n2. Action item 2"
      }}
    }}
    
    CRITICAL: Return ONLY the JSON object, nothing else. No markdown formatting, no code blocks, no explanations.
    - Summary values: concise, human-readable dates and prices in Indian format
    - expected_market_price_inr: must include ₹ symbol and unit (per ton/quintal/kg)
    - Sections: Use \\n for newlines within strings, keep under 220 words total
    """
    try:
        response = requests.post(
            base_url,
            json={
                "contents": [
                    {
                        "parts": [
                            {
                                "text": prompt.strip(),
                            }
                        ]
                    }
                ]
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        ).strip()
        if not text:
            text = "No insights available right now."
        
        # Try to extract JSON from the response (handle markdown code blocks)
        json_text = text
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                json_text = text[start:end].strip()
        elif "```" in text:
            start = text.find("```") + 3
            end = text.find("```", start)
            if end > start:
                json_text = text[start:end].strip()
        
        # Try to find JSON object in the text
        if "{" in json_text and "}" in json_text:
            start_brace = json_text.find("{")
            end_brace = json_text.rfind("}")
            if end_brace > start_brace:
                json_text = json_text[start_brace:end_brace + 1]
        
        payload = {"summary": {}, "sections": {}}
        try:
            payload = json.loads(json_text)
            # Validate that we got actual data
            if not payload.get("summary") or not isinstance(payload.get("summary"), dict):
                payload["summary"] = {}
            if not payload.get("sections") or not isinstance(payload.get("sections"), dict):
                payload["sections"] = {}
        except (json.JSONDecodeError, ValueError) as e:
            # If JSON parsing fails, try to extract summary fields manually
            summary = {}
            sections = {"complete": text}
            
            # Try to extract key fields using regex (handle escaped quotes and newlines)
            # Extract planting date - handle escaped quotes
            planting_match = re.search(r'"optimal_planting_date"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE)
            if planting_match:
                summary["optimal_planting_date"] = planting_match.group(1).replace('\\"', '"').replace('\\n', '\n')
            
            # Extract harvest date
            harvest_match = re.search(r'"expected_harvest_date"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE)
            if harvest_match:
                summary["expected_harvest_date"] = harvest_match.group(1).replace('\\"', '"').replace('\\n', '\n')
            
            # Extract market price
            price_match = re.search(r'"expected_market_price_inr"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE)
            if price_match:
                summary["expected_market_price_inr"] = price_match.group(1).replace('\\"', '"').replace('\\n', '\n')
            
            # Extract irrigation method
            irrigation_match = re.search(r'"irrigation_method"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE)
            if irrigation_match:
                summary["irrigation_method"] = irrigation_match.group(1).replace('\\"', '"').replace('\\n', '\n')
            
            # Extract watering frequency
            frequency_match = re.search(r'"watering_frequency"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE)
            if frequency_match:
                summary["watering_frequency"] = frequency_match.group(1).replace('\\"', '"').replace('\\n', '\n')
            
            # Try to extract sections (handle multi-line strings with escaped newlines)
            market_match = re.search(r'"market_timed"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE | re.DOTALL)
            if market_match:
                sections["market_timed"] = market_match.group(1).replace('\\n', '\n').replace('\\"', '"')
            
            weather_match = re.search(r'"weather_soil"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE | re.DOTALL)
            if weather_match:
                sections["weather_soil"] = weather_match.group(1).replace('\\n', '\n').replace('\\"', '"')
            
            demand_match = re.search(r'"demand_outlook"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE | re.DOTALL)
            if demand_match:
                sections["demand_outlook"] = demand_match.group(1).replace('\\n', '\n').replace('\\"', '"')
            
            timeline_match = re.search(r'"timeline"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE | re.DOTALL)
            if timeline_match:
                sections["timeline"] = timeline_match.group(1).replace('\\n', '\n').replace('\\"', '"')
            
            actions_match = re.search(r'"actions"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE | re.DOTALL)
            if actions_match:
                sections["actions"] = actions_match.group(1).replace('\\n', '\n').replace('\\"', '"')
            
            payload = {"summary": summary, "sections": sections}
        
        summary = payload.get("summary") or {}
        sections = payload.get("sections") or {}
        
        # If summary is empty but we have the raw text, try regex extraction as fallback
        if not summary and text:
            # Try regex extraction one more time on the original text
            planting_match = re.search(r'"optimal_planting_date"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE)
            if planting_match and not summary.get("optimal_planting_date"):
                summary["optimal_planting_date"] = planting_match.group(1).replace('\\"', '"').replace('\\n', '\n')
            
            harvest_match = re.search(r'"expected_harvest_date"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE)
            if harvest_match and not summary.get("expected_harvest_date"):
                summary["expected_harvest_date"] = harvest_match.group(1).replace('\\"', '"').replace('\\n', '\n')
            
            price_match = re.search(r'"expected_market_price_inr"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE)
            if price_match and not summary.get("expected_market_price_inr"):
                summary["expected_market_price_inr"] = price_match.group(1).replace('\\"', '"').replace('\\n', '\n')
            
            irrigation_match = re.search(r'"irrigation_method"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE)
            if irrigation_match and not summary.get("irrigation_method"):
                summary["irrigation_method"] = irrigation_match.group(1).replace('\\"', '"').replace('\\n', '\n')
            
            frequency_match = re.search(r'"watering_frequency"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.IGNORECASE)
            if frequency_match and not summary.get("watering_frequency"):
                summary["watering_frequency"] = frequency_match.group(1).replace('\\"', '"').replace('\\n', '\n')
        combined_markdown = "\n\n".join(
            [section for section in sections.values() if section]
        )
        if not combined_markdown:
            combined_markdown = text
        return {
            "summary": summary,
            "sections": sections,
            "markdown": combined_markdown,
            "raw_text": text,
        }
    except requests.RequestException as exc:
        return {
            "summary": {},
            "sections": {"error": f"Gemini API error: {exc}"},
            "markdown": f"Gemini API error: {exc}",
            "raw_text": str(exc),
        }


@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "").strip()
        farm_name = request.form.get("farm_name", "").strip()

        if not username or not password:
            flash("Username & password are required.", "danger")
        elif username in users:
            flash("That username already exists.", "warning")
        else:
            users[username] = {"password": password, "farm_name": farm_name}
            user_histories[username] = []
            flash("Account created! Please log in 🌱", "success")
            return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "").strip()

        user = users.get(username)
        if not user or user["password"] != password:
            flash("Invalid credentials, try again.", "danger")
        else:
            session["username"] = username
            flash("Welcome back to AgroPulse!", "success")
            return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Signed out successfully. See you soon!", "info")
    return redirect(url_for("landing"))


@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    username = session["username"]
    result = None

    if request.method == "POST":
        crop = request.form.get("crop_name", "").strip()
        size = request.form.get("land_size", "").strip()
        lat = request.form.get("latitude")
        lon = request.form.get("longitude")
        city_name = request.form.get("city_name", "").strip()
        used_fallback = False

        if not crop or not size:
            flash("Please fill in both crop and land size.", "danger")
        else:
            if not lat or not lon:
                lat = DEFAULT_LAT
                lon = DEFAULT_LON
                used_fallback = True

            weather = fetch_weather(lat, lon)
            location_name = (
                city_name
                or weather.get("city")
                or (DEFAULT_CITY if used_fallback else f"Lat {lat}, Lon {lon}")
            )
            if used_fallback and weather:
                weather.setdefault("city", DEFAULT_CITY)
            if used_fallback and not city_name:
                location_name = DEFAULT_CITY
            plan_payload = generate_crop_plan(crop, size, location_name, weather)
            insights_md = plan_payload.get("markdown", "")
            summary = plan_payload.get("summary") or {}
            sections = plan_payload.get("sections") or {}
            insights_html = markdown2.markdown(
                insights_md,
                extras=["fenced-code-blocks", "tables"],
            )
            # Render each section to HTML
            sections_html = {}
            for key, content in sections.items():
                if content:
                    sections_html[key] = markdown2.markdown(
                        content,
                        extras=["fenced-code-blocks", "tables"],
                    )
            result = {
                "crop": crop,
                "land_size": size,
                "location_name": location_name,
                "insights_markdown": insights_md,
                "insights_html": insights_html,
                "sections": sections,
                "sections_html": sections_html,
                "summary": summary,
                "weather": weather,
                "timestamp": datetime.utcnow(),
                "lat": lat,
                "lon": lon,
            }
            user_histories.setdefault(username, []).insert(0, result)

    history = user_histories.get(username, [])[:5]
    for item in history:
        if "insights_html" not in item:
            source_markdown = item.get("insights_markdown") or item.get("insights", "")
            item["insights_html"] = markdown2.markdown(
                source_markdown,
                extras=["fenced-code-blocks", "tables"],
            )
        if "sections_html" not in item and item.get("sections"):
            sections_html = {}
            for key, content in item["sections"].items():
                if content:
                    sections_html[key] = markdown2.markdown(
                        content,
                        extras=["fenced-code-blocks", "tables"],
                    )
            item["sections_html"] = sections_html
        if "location_name" not in item:
            item["location_name"] = item.get("location", "Location not provided")
        if "weather" not in item:
            item["weather"] = {}
        if "summary" not in item:
            item["summary"] = {}
    if not result and history:
        result = history[0]

    farm_name = users.get(username, {}).get("farm_name", "AgroPulse Farm")
    current_weather = (result or {}).get("weather") or {}
    return render_template(
        "dashboard.html",
        active_result=result,
        history=history,
        farm_name=farm_name,
        username=username,
        weather=current_weather,
        default_location={"lat": DEFAULT_LAT, "lon": DEFAULT_LON, "city": DEFAULT_CITY},
    )


@app.route("/api/weather")
@login_required
def api_weather():
    lat = request.args.get("lat")
    lon = request.args.get("lon")
    if not lat or not lon:
        return jsonify({"error": "latitude and longitude required"}), 400
    snapshot = fetch_weather(lat, lon)
    if not snapshot:
        return jsonify({"error": "weather unavailable"}), 502
    return jsonify(snapshot)


@app.route("/api/ping")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5321)))

