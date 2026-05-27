"""
news_fetcher.py — Pobieranie newsów i sentymentu rynkowego
Źródła: Finnhub + Alpha Vantage
"""

import os
import requests
import logging
import finnhub
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

FINNHUB_KEY      = os.getenv("FINNHUB_API_KEY")
ALPHAVANTAGE_KEY = os.getenv("ALPHAVANTAGE_API_KEY")

# Mapowanie instrumentów Capital.com → symbole newsów
SYMBOL_MAP = {
    "EURUSD":     {"finnhub": "forex",  "av": "EURUSD", "keywords": "EUR USD euro dollar forex"},
    "GOLD":       {"finnhub": "forex",  "av": "GOLD",   "keywords": "gold XAU precious metals"},
    "SILVER":     {"finnhub": "forex",  "av": "SILVER", "keywords": "silver XAG precious metals"},
    "NATURALGAS": {"finnhub": "forex",  "av": "NGAS",   "keywords": "natural gas energy"},
    "OIL_CRUDE":  {"finnhub": "forex",  "av": "USOIL",  "keywords": "crude oil WTI OPEC petroleum"},
    "OIL_BRENT":  {"finnhub": "forex",  "av": "USOIL",  "keywords": "brent oil OPEC petroleum"},
    "COPPER":     {"finnhub": "forex",  "av": "COPPER", "keywords": "copper metal mining China"},
    "PLATINUM":   {"finnhub": "forex",  "av": "PLAT",   "keywords": "platinum palladium precious metals"},
    "NVDA":       {"finnhub": "general","av": "NVDA",   "keywords": "NVIDIA AI chips semiconductor GPU"},
    "AAPL":       {"finnhub": "general","av": "AAPL",   "keywords": "Apple iPhone earnings revenue"},
    "TSLA":       {"finnhub": "general","av": "TSLA",   "keywords": "Tesla electric vehicle Elon Musk"},
    "MSFT":       {"finnhub": "general","av": "MSFT",   "keywords": "Microsoft Azure cloud AI earnings"},
    "AMZN":       {"finnhub": "general","av": "AMZN",   "keywords": "Amazon AWS cloud earnings revenue"},
}

finnhub_client = finnhub.Client(api_key=FINNHUB_KEY)

# Cache newsów — TTL 30 minut (oszczędność kosztów API)
import time as _time
_NEWS_CACHE: dict = {}
NEWS_TTL = 1800  # 30 minut


def get_finnhub_news(symbol: str) -> list:
    """Pobiera ostatnie newsy z Finnhub dla danego symbolu"""
    try:
        # Pobierz newsy z ostatnich 24 godzin
        today     = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # Newsy ogólne rynkowe (forex/commodity)
        news = finnhub_client.general_news("forex", min_id=0)

        # Filtruj po słowach kluczowych dla danego instrumentu
        keywords = SYMBOL_MAP.get(symbol, {}).get("keywords", "").lower().split()
        filtered = []
        for article in news[:20]:
            headline = article.get("headline", "").lower()
            summary  = article.get("summary",  "").lower()
            if any(kw in headline or kw in summary for kw in keywords):
                filtered.append({
                    "title":   article.get("headline", ""),
                    "summary": article.get("summary",  "")[:200],
                    "source":  article.get("source",   ""),
                    "time":    datetime.fromtimestamp(
                                   article.get("datetime", 0)
                               ).strftime("%H:%M"),
                })

        return filtered[:5]  # Maksymalnie 5 newsów

    except Exception as e:
        logging.warning(f"Finnhub błąd dla {symbol}: {e}")
        return []


def get_alphavantage_sentiment(symbol: str) -> dict:
    """Pobiera sentyment AI z Alpha Vantage"""
    try:
        av_symbol = SYMBOL_MAP.get(symbol, {}).get("av", symbol)
        keywords  = SYMBOL_MAP.get(symbol, {}).get("keywords", symbol)

        url = "https://www.alphavantage.co/query"
        params = {
            "function":   "NEWS_SENTIMENT",
            "tickers":    av_symbol,
            "topics":     "forex,financial_markets,economy_macro",
            "limit":      5,
            "apikey":     ALPHAVANTAGE_KEY,
        }
        r = requests.get(url, params=params, timeout=10)
        data = r.json()

        feed = data.get("feed", [])
        if not feed:
            return {"sentiment": "neutral", "score": 0.0, "articles": 0}

        # Oblicz średni sentyment
        scores = []
        for article in feed[:5]:
            score = float(article.get("overall_sentiment_score", 0))
            scores.append(score)

        avg_score = sum(scores) / len(scores) if scores else 0

        if avg_score > 0.15:
            sentiment = "bullish"
        elif avg_score < -0.15:
            sentiment = "bearish"
        else:
            sentiment = "neutral"

        return {
            "sentiment": sentiment,
            "score":     round(avg_score, 3),
            "articles":  len(scores),
        }

    except Exception as e:
        logging.warning(f"Alpha Vantage błąd dla {symbol}: {e}")
        return {"sentiment": "neutral", "score": 0.0, "articles": 0}


def get_news_context(symbol: str) -> dict:
    """
    Główna funkcja — zwraca pełny kontekst newsowy dla danego instrumentu.
    Wywoływana z bot.py przed zapytaniem Claude.
    Wyniki cachowane przez 30 minut.
    """
    cached = _NEWS_CACHE.get(symbol)
    if cached and (_time.time() - cached[0]) < NEWS_TTL:
        return cached[1]

    news      = get_finnhub_news(symbol)
    sentiment = get_alphavantage_sentiment(symbol)

    result = {
        "symbol":         symbol,
        "news_sentiment": sentiment["sentiment"],
        "sentiment_score":sentiment["score"],
        "articles_count": sentiment["articles"],
        "latest_news":    news,
    }
    _NEWS_CACHE[symbol] = (_time.time(), result)
    return result


if __name__ == "__main__":
    # Test
    for symbol in ["EURUSD", "GOLD", "OIL_CRUDE"]:
        print(f"\n{'='*40}")
        print(f"Newsy dla: {symbol}")
        ctx = get_news_context(symbol)
        print(f"Sentyment: {ctx['news_sentiment']} (score: {ctx['sentiment_score']})")
        print(f"Artykułów: {ctx['articles_count']}")
        if ctx["latest_news"]:
            print("Ostatnie newsy:")
            for n in ctx["latest_news"]:
                print(f"  [{n['time']}] {n['title'][:80]}")
        else:
            print("Brak newsów dla tego instrumentu")
