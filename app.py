from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import anthropic
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_KEY")
EODHD_KEY      = os.environ.get("EODHD_KEY")
FINNHUB_KEY    = os.environ.get("FINNHUB_KEY")
MASSIVE_KEY    = os.environ.get("MASSIVE_KEY")

ROLES = {
    "quant": {
        "label": "Quantitative Analyst",
        "topics": ["economy_macro", "finance", "technology"],
        "context": "You are briefing a quantitative analyst who builds systematic trading strategies. Focus on volatility regime shifts, economic indicator surprises, factor model performance, and anything affecting systematic strategies."
    },
    "day_trader": {
        "label": "Day Trader",
        "topics": ["earnings", "mergers_and_acquisitions", "finance"],
        "context": "You are briefing an active day trader. Focus on earnings surprises, momentum plays, breaking corporate news, analyst upgrades and downgrades, and anything likely to cause sharp intraday price moves today."
    },
    "hedge_fund_pm": {
        "label": "Hedge Fund PM",
        "topics": ["economy_macro", "finance", "energy_transportation"],
        "context": "You are briefing a hedge fund portfolio manager overseeing a multi-strategy fund. Focus on macro trends, geopolitical risks, central bank signals, sector rotation opportunities, and systemic risks."
    },
    "wealth_manager": {
        "label": "Wealth Manager",
        "topics": ["economy_macro", "finance", "real_estate"],
        "context": "You are briefing a wealth manager advising high-net-worth clients. Focus on broad market health, Fed policy changes, inflation trends, and anything that affects long-term portfolio allocation decisions."
    }
}


def fetch_news(limit=20):
    # Try Massive/Polygon first
    try:
        url = "https://api.polygon.io/v2/reference/news"
        params = {"limit": limit, "order": "desc", "sort": "published_utc", "apiKey": MASSIVE_KEY}
        r = requests.get(url, params=params, timeout=8)
        data = r.json()
        if "results" in data and len(data["results"]) > 0:
            articles = []
            for item in data["results"]:
                articles.append({
                    "title": item.get("title", ""),
                    "summary": item.get("description", ""),
                    "source": item.get("publisher", {}).get("name", ""),
                    "sentiment": "Neutral",
                    "topics": ["finance", "economy_macro"],
                    "tickers": item.get("tickers", [])
                })
            print(f"Massive: fetched {len(articles)} articles")
            return articles
    except Exception as e:
        print(f"Massive failed: {e}")

    # Fallback to Finnhub
    try:
        url = "https://finnhub.io/api/v1/news"
        params = {"category": "general", "token": FINNHUB_KEY}
        r = requests.get(url, params=params, timeout=8)
        data = r.json()
        articles = []
        for item in data[:limit]:
            articles.append({
                "title": item.get("headline", ""),
                "summary": item.get("summary", ""),
                "source": item.get("source", ""),
                "sentiment": "Neutral",
                "topics": ["finance", "economy_macro"],
                "tickers": []
            })
        print(f"Finnhub: fetched {len(articles)} articles")
        return articles
    except Exception as e:
        print(f"Finnhub failed: {e}")
        return []


def fetch_market_data():
    tickers = {
        "S&P 500": "GSPC.INDX",
        "NASDAQ":  "IXIC.INDX",
        "Gold":    "XAUUSD.FOREX"
    }
    results = {}
    for name, symbol in tickers.items():
        try:
            url = f"https://eodhd.com/api/real-time/{symbol}"
            params = {"api_token": EODHD_KEY, "fmt": "json"}
            r = requests.get(url, params=params, timeout=8)
            d = r.json()
            results[name] = {
                "close": d.get("close", "N/A"),
                "change_pct": d.get("change_p", "N/A")
            }
        except Exception as e:
            results[name] = {"close": "N/A", "change_pct": "N/A"}
    return results


def generate_digest(role_key, articles, market_data):
    role = ROLES[role_key]

    relevant = [a for a in articles if any(t in role["topics"] for t in a["topics"])][:10]
    if not relevant:
        relevant = articles[:10]

    news_text = ""
    for i, a in enumerate(relevant, 1):
        news_text += f"{i}. {a['title']}\n"
        news_text += f"   Source: {a['source']}\n"
        news_text += f"   {a['summary'][:250]}\n\n"

    market_text = "\n".join([
        f"{k}: {v['close']} ({v['change_pct']}% change)"
        for k, v in market_data.items()
    ])

    prompt = f"""You are a senior financial analyst generating a market digest.

{role['context']}

TODAY'S MARKET SNAPSHOT:
{market_text}

LATEST NEWS:
{news_text}

Generate a JSON response with exactly this structure:
{{
  "headline": "one punchy headline summarizing the most important thing for this role (max 10 words)",
  "deck": "one sentence expanding on the headline (max 20 words)",
  "overview": "2-3 sentence market overview relevant to this role",
  "stories": [
    {{"ticker": "TICKER or MACRO", "headline": "story headline", "why": "why this matters for this specific role (2 sentences)"}},
    {{"ticker": "TICKER or MACRO", "headline": "story headline", "why": "why this matters for this specific role (2 sentences)"}},
    {{"ticker": "TICKER or MACRO", "headline": "story headline", "why": "why this matters for this specific role (2 sentences)"}}
  ],
  "strategy": "2-3 sentences of specific actionable insight for this role based on today's news and market data",
  "watchlist": [
    "specific thing to watch in next 24 hours with context",
    "specific thing to watch in next 24 hours with context",
    "specific thing to watch in next 24 hours with context"
  ]
}}

Respond with valid JSON only. No markdown, no backticks, no extra text."""

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}]
    )

    import json
    raw = message.content[0].text.strip()
    return json.loads(raw)


@app.route("/api/digest", methods=["POST"])
def digest():
    try:
        body = request.get_json()
        role_key = body.get("role", "day_trader")
        cadence = body.get("cadence", "Daily")

        if role_key not in ROLES:
            return jsonify({"error": "Invalid role"}), 400

        articles = fetch_news()
        market_data = fetch_market_data()
        digest_data = generate_digest(role_key, articles, market_data)

        return jsonify({
            "role": ROLES[role_key]["label"],
            "cadence": cadence,
            "generated_at": datetime.utcnow().isoformat(),
            "market": market_data,
            "digest": digest_data
        })

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
