"""
social_monitor.py — Monitoring Google News RSS + Reddit
Śledzi newsy polityczne i rynkowe wpływające na CFD
"""

import os
import logging
import feedparser
import praw
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

# ── Konfiguracja Reddit ──────────────────────────────────────────────────────
REDDIT_CLIENT_ID     = os.getenv("REDDIT_CLIENT_ID",     "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT    = "trading-bot/1.0"

# ── Google News RSS — tematy per instrument ──────────────────────────────────
GOOGLE_NEWS_FEEDS = {
    "SILVER":     [
        "https://news.google.com/rss/search?q=silver+price+XAG+precious+metals&hl=en&gl=US&ceid=US:en",
    ],
    "OIL_BRENT":  [
        "https://news.google.com/rss/search?q=brent+oil+OPEC+crude&hl=en&gl=US&ceid=US:en",
    ],
    "COPPER":     [
        "https://news.google.com/rss/search?q=copper+price+metal+China&hl=en&gl=US&ceid=US:en",
    ],
    "PLATINUM":   [
        "https://news.google.com/rss/search?q=platinum+palladium+precious+metals&hl=en&gl=US&ceid=US:en",
    ],
    "NVDA":       [
        "https://news.google.com/rss/search?q=NVIDIA+AI+chips+GPU+earnings&hl=en&gl=US&ceid=US:en",
    ],
    "AAPL":       [
        "https://news.google.com/rss/search?q=Apple+iPhone+earnings+revenue&hl=en&gl=US&ceid=US:en",
    ],
    "TSLA":       [
        "https://news.google.com/rss/search?q=Tesla+electric+vehicle+Elon+Musk&hl=en&gl=US&ceid=US:en",
    ],
    "MSFT":       [
        "https://news.google.com/rss/search?q=Microsoft+Azure+cloud+earnings&hl=en&gl=US&ceid=US:en",
    ],
    "AMZN":       [
        "https://news.google.com/rss/search?q=Amazon+AWS+cloud+earnings&hl=en&gl=US&ceid=US:en",
    ],
}

# ── Subreddity per instrument ─────────────────────────────────────────────────
REDDIT_SOURCES = {
    "EURUSD":     ["forex",     "economics",      "worldnews"],
    "GOLD":       ["investing", "gold",            "economics"],
    "SILVER":     ["investing", "Silverbugs",      "gold"],
    "NATURALGAS": ["investing", "energy",          "worldnews"],
    "OIL_CRUDE":  ["investing", "energy",          "worldnews"],
    "OIL_BRENT":  ["investing", "energy",          "worldnews"],
    "COPPER":     ["investing", "Commodities",     "economics"],
    "PLATINUM":   ["investing", "Silverbugs",      "gold"],
    "NVDA":       ["stocks",    "investing",       "nvidia"],
    "AAPL":       ["stocks",    "apple",           "investing"],
    "TSLA":       ["stocks",    "teslamotors",     "investing"],
    "MSFT":       ["stocks",    "investing",       "technology"],
    "AMZN":       ["stocks",    "investing",       "technology"],
}

# ── Słowa kluczowe per instrument ─────────────────────────────────────────────
KEYWORDS = {
    "SILVER":     ["silver", "XAG", "precious metals"],
    "OIL_BRENT":  ["brent", "oil", "OPEC", "crude"],
    "COPPER":     ["copper", "metal", "mining", "China"],
    "PLATINUM":   ["platinum", "palladium", "precious"],
    "NVDA":       ["NVIDIA", "AI", "chips", "GPU", "semiconductor"],
    "AAPL":       ["Apple", "iPhone", "Mac", "earnings"],
    "TSLA":       ["Tesla", "electric", "Elon", "EV"],
    "MSFT":       ["Microsoft", "Azure", "cloud", "AI"],
    "AMZN":       ["Amazon", "AWS", "cloud", "Prime"],
}


def _is_recent(published_str: str, hours: int = 6) -> bool:
    """Sprawdza czy news jest z ostatnich X godzin"""
    try:
        import email.utils
        dt = email.utils.parsedate_to_datetime(published_str)
        now = datetime.now(timezone.utc)
        return (now - dt) < timedelta(hours=hours)
    except Exception:
        return True


def get_google_news(symbol: str, max_items: int = 5) -> list:
    """Pobiera najnowsze newsy z Google News RSS"""
    feeds   = GOOGLE_NEWS_FEEDS.get(symbol, [])
    results = []

    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                title     = entry.get("title",   "")
                published = entry.get("published", "")

                if not _is_recent(published, hours=6):
                    continue

                # Filtruj po słowach kluczowych
                keywords = KEYWORDS.get(symbol, [])
                if not any(kw.lower() in title.lower() for kw in keywords):
                    continue

                results.append({
                    "source":  "Google News",
                    "title":   title,
                    "time":    published[:16] if published else "?",
                    "url":     entry.get("link", ""),
                })

                if len(results) >= max_items:
                    return results

        except Exception as e:
            logging.warning(f"Google News błąd dla {symbol}: {e}")

    return results


def get_reddit_posts(symbol: str, max_items: int = 3) -> list:
    """Pobiera gorące posty z Reddit"""
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        return []

    try:
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT,
        )

        subreddits = REDDIT_SOURCES.get(symbol, ["investing"])
        keywords   = KEYWORDS.get(symbol, [])
        results    = []

        for sub in subreddits:
            try:
                subreddit = reddit.subreddit(sub)
                for post in subreddit.hot(limit=20):
                    title = post.title

                    if not any(kw.lower() in title.lower() for kw in keywords):
                        continue
                    if post.score < 50:
                        continue

                    results.append({
                        "source":  f"Reddit r/{sub}",
                        "title":   title,
                        "score":   post.score,
                        "time":    datetime.fromtimestamp(
                                       post.created_utc, tz=timezone.utc
                                   ).strftime("%H:%M"),
                    })

                    if len(results) >= max_items:
                        return results

            except Exception:
                continue

    except Exception as e:
        logging.warning(f"Reddit błąd dla {symbol}: {e}")

    return results


def analyze_sentiment(texts: list) -> dict:
    """
    Prosta analiza sentymentu na podstawie słów kluczowych.
    Zwraca: bullish / bearish / neutral + score
    """
    bullish_words = [
        "surge", "rally", "gains", "rises", "bullish", "strong",
        "higher", "positive", "growth", "recovery", "boost",
        "optimism", "record", "jump", "soars"
    ]
    bearish_words = [
        "fall", "drop", "crash", "bearish", "weak", "lower",
        "negative", "recession", "crisis", "fear", "risk",
        "decline", "plunge", "tumble", "tariff", "sanction", "war"
    ]

    score = 0
    for text in texts:
        text_lower = text.lower()
        score += sum(1 for w in bullish_words if w in text_lower)
        score -= sum(1 for w in bearish_words if w in text_lower)

    if score > 2:
        sentiment = "bullish"
    elif score < -2:
        sentiment = "bearish"
    else:
        sentiment = "neutral"

    return {"sentiment": sentiment, "score": score}


def get_social_context(symbol: str) -> dict:
    """
    Główna funkcja — zwraca pełny kontekst społecznościowy.
    Wywoływana z bot.py razem z get_news_context().
    """
    google_news   = get_google_news(symbol,  max_items=5)
    reddit_posts  = get_reddit_posts(symbol, max_items=3)

    all_titles    = [n["title"] for n in google_news + reddit_posts]
    sentiment     = analyze_sentiment(all_titles)

    return {
        "symbol":        symbol,
        "google_news":   google_news,
        "reddit_posts":  reddit_posts,
        "sentiment":     sentiment["sentiment"],
        "score":         sentiment["score"],
        "total_sources": len(google_news) + len(reddit_posts),
    }


if __name__ == "__main__":
    print("🔍 Test Social Monitor\n")
    for symbol in ["EURUSD", "GOLD", "OIL_CRUDE"]:
        print(f"{'='*50}")
        print(f"Symbol: {symbol}")
        ctx = get_social_context(symbol)
        print(f"Sentyment: {ctx['sentiment']} (score: {ctx['score']})")
        print(f"Źródeł: {ctx['total_sources']}")
        if ctx["google_news"]:
            print("📰 Google News:")
            for n in ctx["google_news"]:
                print(f"  {n['title'][:80]}")
        if ctx["reddit_posts"]:
            print("🔴 Reddit:")
            for r in ctx["reddit_posts"]:
                print(f"  [{r['score']} upvotes] {r['title'][:70]}")
        print()
