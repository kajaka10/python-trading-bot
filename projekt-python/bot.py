"""
bot.py — Główna pętla AI Trading Bota
"""

import os
import json
import time
import logging
import requests
import schedule
from datetime import datetime, timezone
from dotenv import load_dotenv
from openai import OpenAI

from capital_client import CapitalClient
from reporter import generate_report
from notifier import (alert_bot_started, alert_trade_opened,
                      alert_error, alert_daily_summary, send_message)
import trade_logger
from news_fetcher import get_news_context
from social_monitor import get_social_context
from indicators import calculate_indicators

load_dotenv()

logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)


SYMBOLS = ["GOLD", "NVDA", "MSFT", "COPPER", "US500"]
INTERVAL_MINUTES = 15

POSITION_SIZE = {
    # Surowce
    "GOLD":       0.01,
    "SILVER":     1,
    "OIL_CRUDE":  1,
    "OIL_BRENT":  1,
    "NATURALGAS": 10,
    "COPPER":     10,
    "PLATINUM":   0.01,
    # Indeksy
    "US500":      0.1,
    "US100":      0.1,
    # Akcje
    "NVDA":       0.1,
    "AAPL":       0.1,
    "TSLA":       0.1,
    "MSFT":       0.01,
    "AMZN":       0.1,
}

MAX_OPEN_POSITIONS = 2
MAX_OPEN_POSITIONS_HARD = 2  # Twardy limit
MAX_CAPITAL_AT_RISK = 230.0

STATS = {
    "EURUSD":     {"wins": 0, "losses": 0, "pnl": 0.0},
    "GOLD":       {"wins": 0, "losses": 0, "pnl": 0.0},
    "NATURALGAS": {"wins": 0, "losses": 0, "pnl": 0.0},
    "OIL_CRUDE":  {"wins": 0, "losses": 0, "pnl": 0.0},
    "NVDA":       {"wins": 0, "losses": 0, "pnl": 0.0},
    "TSLA":       {"wins": 0, "losses": 0, "pnl": 0.0},
    "SILVER":     {"wins": 0, "losses": 0, "pnl": 0.0},
    "MSFT":       {"wins": 0, "losses": 0, "pnl": 0.0},
    "OIL_BRENT":  {"wins": 0, "losses": 0, "pnl": 0.0},
}

capital = CapitalClient()
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Cooldown per instrument — czas ostatniego zamknięcia
LAST_CLOSE_TIME = {}
COOLDOWN_MINUTES = 30
COOLDOWN_FILE = os.path.join(os.path.dirname(__file__), "cooldown_state.json")
KNOWN_POSITIONS = {}  # {deal_id: {"symbol": ..., "direction": ..., "size": ...}}

def load_cooldowns():
    """Wczytuje cooldowny z pliku (przeżywa restart bota)"""
    global LAST_CLOSE_TIME
    try:
        import json as _json
        with open(COOLDOWN_FILE) as f:
            raw = _json.load(f)
        from datetime import timezone as _tz
        LAST_CLOSE_TIME = {
            k: datetime.fromisoformat(v).replace(tzinfo=_tz.utc)
            for k, v in raw.items()
        }
        logging.info(f"Wczytano cooldowny: {list(LAST_CLOSE_TIME.keys())}")
    except FileNotFoundError:
        pass
    except Exception as e:
        logging.warning(f"Błąd wczytywania cooldownów: {e}")

def save_cooldowns():
    """Zapisuje cooldowny do pliku"""
    try:
        import json as _json
        with open(COOLDOWN_FILE, "w") as f:
            _json.dump({k: v.isoformat() for k, v in LAST_CLOSE_TIME.items()}, f)
    except Exception as e:
        logging.warning(f"Błąd zapisywania cooldownów: {e}")

def is_trading_hours():
    now     = datetime.now(timezone.utc)
    weekday = now.weekday()
    hour    = now.hour
    if weekday == 5:
        return False
    if weekday == 6 and hour < 22:
        return False
    if weekday == 4 and hour >= 21:
        return False
    return True


def get_account_balance():
    try:
        capital.keep_alive()
        r = requests.get(
            "https://demo-api-capital.backend-capital.com/api/v1/accounts",
            headers=capital._headers()
        )
        r.raise_for_status()
        accounts = r.json().get("accounts", [])
        if accounts:
            return accounts[0].get("balance", {}).get("balance", 0)
    except Exception as e:
        logging.error(f"Blad pobierania salda: {e}")
    return 0


def get_open_positions_details():
    try:
        positions = capital.get_positions()
        return positions.get("positions", [])
    except Exception:
        return []

def get_real_pnl(deal_id: str, symbol: str = "") -> float:
    """Pobiera prawdziwy P&L z historii transakcji po symbolu (ostatnia zamknięta)"""
    try:
        import requests as req
        r = req.get(
            "https://demo-api-capital.backend-capital.com/api/v1/history/transactions",
            headers=capital._headers(),
            params={"lastPeriod": 3600}
        )
        if not r.ok:
            return 0.0
        transactions = r.json().get("transactions", [])
        # Szukaj po dealId lub reference
        for tx in transactions:
            if tx.get("dealId") == deal_id or tx.get("reference") == deal_id:
                return float(tx.get("size", 0))
        # Fallback: ostatnia zamknięta transakcja dla tego symbolu
        if symbol:
            sym_clean = symbol.replace("_", " ").upper()
            for tx in transactions:
                inst = tx.get("instrumentName", "").upper().replace("_", " ")
                if (sym_clean in inst or inst in sym_clean) and tx.get("note") == "Trade closed":
                    return float(tx.get("size", 0))
        return 0.0
    except Exception as e:
        logging.warning(f"Blad pobierania P&L: {e}")
        return 0.0

def check_and_close_positions():
    """Sprawdza otwarte pozycje i zamyka gdy sygnał się odwraca.
    Wykrywa też zamknięcia przez SL/TP porównując z poprzednim stanem."""
    global KNOWN_POSITIONS

    # Detekcja SL/TP przez porównanie z poprzednim stanem KNOWN_POSITIONS
    try:
        current_raw = get_open_positions_details()
        current_ids = {p["position"]["dealId"] for p in current_raw}

        for deal_id, info in list(KNOWN_POSITIONS.items()):
            if deal_id not in current_ids:
                symbol = info["symbol"]
                direction = info["direction"]
                pnl = get_real_pnl(deal_id, symbol)
                if pnl == 0.0:
                    pnl = info.get("pnl", 0)
                logging.info(f"{symbol}: Pozycja {deal_id} ({direction}) zamknięta przez SL/TP, P&L={pnl}")
                # Loguj zamknięcie do trade_logger ze spreadem
                try:
                    snap = capital.get_price(symbol).get("snapshot", {})
                    trade_logger.log_trade_close(
                        deal_id=deal_id,
                        bid=float(snap.get("bid", 0) or 0),
                        ask=float(snap.get("offer", 0) or 0),
                        gross_pnl=float(pnl),
                        symbol=symbol,
                        direction=direction,
                        size=float(info.get("size", 0) or 0),
                    )
                except Exception as log_err:
                    logging.warning(f"trade_logger close blad: {log_err}")
                try:
                    from notifier import alert_trade_closed
                    alert_trade_closed(
                        symbol=symbol,
                        action=direction,
                        pnl=float(pnl),
                        reason="TP hit" if pnl > 0 else "SL hit"
                    )
                except Exception as alert_err:
                    logging.error(f"Blad alertu Telegram: {alert_err}")
                LAST_CLOSE_TIME[symbol] = datetime.now(timezone.utc)
                save_cooldowns()
                del KNOWN_POSITIONS[deal_id]

        # Dodaj nowe pozycje do KNOWN_POSITIONS
        for p in current_raw:
            did = p.get("position", {}).get("dealId", "")
            if did and did not in KNOWN_POSITIONS:
                KNOWN_POSITIONS[did] = {
                    "symbol":      p.get("market",   {}).get("epic",       ""),
                    "direction":   p.get("position", {}).get("direction",  ""),
                    "size":        p.get("position", {}).get("size",        0),
                    "open_level":  p.get("position", {}).get("openLevel",   0),
                }
                logging.info(f"Zarejestrowano pozycję: {KNOWN_POSITIONS[did]}")
                # Loguj otwarcie do trade_logger ze spreadem
                try:
                    sym = KNOWN_POSITIONS[did]["symbol"]
                    snap = capital.get_price(sym).get("snapshot", {})
                    trade_logger.log_trade_open(
                        deal_id=did,
                        symbol=sym,
                        direction=KNOWN_POSITIONS[did]["direction"],
                        size=float(KNOWN_POSITIONS[did]["size"] or 0),
                        bid=float(snap.get("bid", 0) or 0),
                        ask=float(snap.get("offer", 0) or 0),
                        open_level=float(KNOWN_POSITIONS[did]["open_level"] or 0),
                    )
                except Exception as log_err:
                    logging.warning(f"trade_logger open blad: {log_err}")
            elif did in KNOWN_POSITIONS:
                # Aktualizuj P&L przy każdym cyklu
                KNOWN_POSITIONS[did]["pnl"] = p.get("position", {}).get("upl", 0)
    except Exception as e:
        logging.warning(f"Błąd monitorowania SL/TP: {e}")

    positions = get_open_positions_details()
    if not positions:
        return

    for p in positions:
        try:
            symbol    = p.get("market",   {}).get("epic",      "")
            direction = p.get("position", {}).get("direction", "")
            deal_id   = p.get("position", {}).get("dealId",    "")
            pnl       = p.get("position", {}).get("upl",        0)

            if not symbol or not deal_id:
                continue

            # Sygnał odwrócenia sprawdzamy tylko w głównej pętli run_cycle
            # żeby nie dublować wywołań Claude
            if False:
                print(f"  🔄 {symbol}: Odwrócenie sygnału! "
                      f"{direction} → {new_action} | {reason}")
                logging.info(
                    f"{symbol}: Zamykam pozycję {direction} "
                    f"(P&L: {pnl:.2f}) — sygnał odwrócony: {reason}"
                )
                capital.close_position(deal_id)
                LAST_CLOSE_TIME[symbol] = datetime.now(timezone.utc)
                save_cooldowns()

                # Alert Telegram
                try:
                    from notifier import alert_trade_closed
                    alert_trade_closed(
                        symbol=symbol,
                        action=direction,
                        pnl=pnl,
                        reason=f"Odwrócenie sygnału: {reason}"
                    )
                except Exception:
                    pass

                # Aktualizuj statystyki
                if symbol in STATS:
                    if pnl > 0:
                        STATS[symbol]["wins"]  += 1
                    else:
                        STATS[symbol]["losses"] += 1
                    STATS[symbol]["pnl"] += pnl

            time.sleep(2)

        except Exception as e:
            logging.error(f"Błąd sprawdzania pozycji {symbol}: {e}")

def get_market_data(epic):
    price   = capital.get_price(epic)
    candles = capital.get_candles(epic, resolution="MINUTE_15", max=20)
    snapshot = price.get("snapshot", {})
    prices   = candles.get("prices", [])
    candle_summary = [
        {
            "time":   p.get("snapshotTime", ""),
            "open":   round((p.get("openPrice",  {}).get("bid", 0) + p.get("openPrice",  {}).get("ask", 0)) / 2, 5),
            "high":   round((p.get("highPrice",  {}).get("bid", 0) + p.get("highPrice",  {}).get("ask", 0)) / 2, 5),
            "low":    round((p.get("lowPrice",   {}).get("bid", 0) + p.get("lowPrice",   {}).get("ask", 0)) / 2, 5),
            "close":  round((p.get("closePrice", {}).get("bid", 0) + p.get("closePrice", {}).get("ask", 0)) / 2, 5),
            "volume": p.get("lastTradedVolume", 0),
        }
        for p in prices
    ]
    return {
        "symbol":     epic,
        "bid":        snapshot.get("bid",              0),
        "ask":        snapshot.get("offer",            0),
        "candles":    candle_summary,
        "high_24h":   snapshot.get("high",             0),
        "low_24h":    snapshot.get("low",              0),
        "change_pct": snapshot.get("percentageChange", 0),
    }


def get_trend(candles: list) -> str:
    """Sprawdza trend z ostatnich 4 godzin (16 świec 15-min).
    Zwraca 'UP', 'DOWN' lub 'NEUTRAL'"""
    try:
        if len(candles) < 16:
            return "NEUTRAL"
        closes = [c["close"] for c in candles[-16:]]
        price_now  = closes[-1]
        price_4h   = closes[0]
        change_pct = (price_now - price_4h) / price_4h * 100
        if change_pct > 0.3:
            return "UP"
        elif change_pct < -0.3:
            return "DOWN"
        else:
            return "NEUTRAL"
    except Exception:
        return "NEUTRAL"

def ask_claude(market_data: dict, news_context: dict = None,
               social_context: dict = None, indicators: dict = None) -> dict:
    """Wysyła dane rynkowe + newsy + wskaźniki techniczne do Claude"""

    if news_context and news_context.get("latest_news"):
        news_lines = "\n".join([
            f"  [{n['time']}] {n['title']}"
            for n in news_context["latest_news"]
        ])
        news_section = (
            f"Finnhub sentyment: {news_context['news_sentiment'].upper()}\n"
            f"Newsy:\n{news_lines}"
        )
    else:
        news_section = "Brak newsów Finnhub."

    if social_context and social_context.get("google_news"):
        google_lines = "\n".join([
            f"  {n['title'][:100]}"
            for n in social_context["google_news"]
        ])
        social_section = (
            f"Google News: {social_context['sentiment'].upper()} "
            f"(score: {social_context['score']})\n"
            f"Artykuły:\n{google_lines}"
        )
    else:
        social_section = "Brak newsów Google."

    if indicators and not indicators.get("error"):
        ind_section = f"""
RSI(14):          {indicators.get('rsi')} {'⚠️ OVERSOLD' if indicators.get('rsi', 50) < 30 else '⚠️ OVERBOUGHT' if indicators.get('rsi', 50) > 70 else ''}
MACD:             {indicators.get('macd')} | Signal: {indicators.get('macd_signal')} | Hist: {indicators.get('macd_hist')}
Bollinger Bands:  Upper={indicators.get('bb_upper')} Mid={indicators.get('bb_mid')} Lower={indicators.get('bb_lower')}
EMA9/EMA21:       {indicators.get('ema9')} / {indicators.get('ema21')}
ATR(14):          {indicators.get('atr')}
Sygnały:          {', '.join(indicators.get('signals', ['brak']))}
"""
    else:
        ind_section = "Brak wskaźników technicznych."

    prompt = f"""
Jesteś doświadczonym traderem CFD. Analizujesz dane techniczne ORAZ fundamentalne.
Odpowiadasz TYLKO w formacie JSON — bez żadnego tekstu przed ani po.

Instrument: {market_data['symbol']}
Cena bid: {market_data['bid']}
Cena ask: {market_data['ask']}
Zmiana 24h: {market_data['change_pct']}%
High 24h: {market_data['high_24h']}
Low 24h: {market_data['low_24h']}

=== WSKAŹNIKI TECHNICZNE ===
{ind_section}

=== OSTATNIE 10 ŚWIEC 15-MIN ===
{json.dumps(market_data['candles'], indent=2)}

=== ANALIZA FUNDAMENTALNA ===
{news_section}

=== GOOGLE NEWS ===
{social_section}

Zasady:
- RSI < 30 = mocny sygnał BUY, RSI > 70 = mocny sygnał SELL
- MACD bullish crossover + trend wzrostowy = BUY
- MACD bearish crossover + trend spadkowy = SELL
- Cena poniżej BB dolnego = BUY, powyżej BB górnego = SELL
- Minimum 2 zgodne sygnały żeby otworzyć pozycję
- Jeśli sygnały sprzeczne = WAIT
- Stop loss i take profit MUSZĄ być podane jako ABSOLUTNA CENA, nie jako różnica punktów!
- Przykład dla OIL_CRUDE przy cenie 89.00: stop_loss=88.50, take_profit=90.00
- Przykład dla GOLD przy cenie 4750: stop_loss=4730, take_profit=4790
- Przykład dla SILVER przy cenie 78.00: stop_loss=77.50, take_profit=79.00
- Przykład dla NVDA przy cenie 200.00: stop_loss=198.00, take_profit=204.00
- Przykład dla TSLA przy cenie 388.00: stop_loss=385.00, take_profit=394.00
- Przykład dla MSFT przy cenie 425.00: stop_loss=422.00, take_profit=431.00
- Take profit zawsze minimum 2x odległość stop loss od ceny wejścia
- Jeśli nie jesteś pewien ceny — użyj ATR do obliczenia: SL = cena - 1.5*ATR, TP = cena + 3*ATR
- Take profit min 2x stop loss
- ATR używaj do ustawienia stop loss (1.5x ATR)

Odpowiedz TYLKO w JSON:
{{
  "action": "BUY" | "SELL" | "WAIT",
  "stop_loss": 0.00000,
  "take_profit": 0.00000,
  "confidence": "LOW" | "MEDIUM" | "HIGH",
  "reason": "Uzasadnienie po polsku (max 20 słów)"
}}
"""

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    text = response.choices[0].message.content.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def send_heartbeat():
    """Wysyła co godzinę info że bot działa"""
    try:
        capital.keep_alive()
        positions = get_open_positions_details()
        balance   = get_account_balance()
        pnl_total = sum(p.get("position", {}).get("upl", 0) for p in positions)
        pnl_str   = f"+${pnl_total:.2f}" if pnl_total >= 0 else f"-${abs(pnl_total):.2f}"

        pos_lines = ""
        for p in positions:
            sym = p.get("market",   {}).get("epic",      "?")
            dr  = p.get("position", {}).get("direction", "?")
            pnl = p.get("position", {}).get("upl",        0)
            sl  = p.get("position", {}).get("stopLevel",  0)
            tp  = p.get("position", {}).get("profitLevel",0)
            emoji = "🟢" if dr == "BUY" else "🔴"
            znak  = "+" if pnl >= 0 else ""
            pos_lines += "  " + emoji + " " + sym + " " + dr + chr(10)
            pos_lines += "     Wynik: " + znak + "$" + format(pnl, ".2f") + "   SL: " + str(sl) + "   TP: " + str(tp) + chr(10)
        if not pos_lines:
            pos_lines = "  Brak otwartych pozycji" + chr(10)
        from notifier import send_message
        sep = "━" * 15 + chr(10)
        msg  = "🤖 Bot aktywny" + chr(10)
        msg += sep
        msg += "💰 Saldo:       $" + format(balance, ",.2f") + chr(10)
        msg += "📊 P&L otwarty: " + pnl_str + chr(10)
        msg += sep
        msg += "Pozycje (" + str(len(positions)) + "):" + chr(10)
        msg += pos_lines
        send_message(msg)
    except Exception as e:
        logging.warning(f"Heartbeat nieudany: {e}")


def run_cycle():
    logging.info("=== Nowy cykl analizy ===")
    if not is_trading_hours():
        print(f"\U0001f4a4 Rynek zamkniety")
        return

    print(f"\u23f0 {datetime.now().strftime('%H:%M:%S')} Analizuje rynek...")
    capital.keep_alive()

 # Sprawdź czy zamknąć istniejące pozycje
    check_and_close_positions()

    balance   = get_account_balance()
    positions = get_open_positions_details()
    print(f"  Saldo: ${balance:,.2f} | Pozycje: {len(positions)}")

    for p in positions:
        sym = p.get("market", {}).get("epic", "?")
        dr  = p.get("position", {}).get("direction", "?")
        pnl = p.get("position", {}).get("upl", 0)
        print(f"  {sym}: {dr} | P&L: {pnl:.2f}")

    if len(positions) >= MAX_OPEN_POSITIONS:
        print(f"  ⚠️  Max pozycji ({MAX_OPEN_POSITIONS}) osiągnięte — pomijam analizę AI")
        logging.info(f"Pominięto analizę AI — max pozycji osiągnięte ({len(positions)})")
        return

    total_invested = sum(
        abs(p.get("position", {}).get("dealSize", 0) * p.get("position", {}).get("openLevel", 0))
        for p in positions
    )
    if total_invested >= MAX_CAPITAL_AT_RISK:
        print(f"  Limit kapitalu osiagniety")
        return

    for symbol in SYMBOLS:
        open_position = next(
            (p for p in positions if p.get("market", {}).get("epic", "") == symbol),
            None
        )
        if open_position:
            open_direction = open_position.get("position", {}).get("direction", "")
            print(f"  {symbol}: juz otwarta pozycja {open_direction} — pomijam")
            continue
        # Sprawdź cooldown
        last_close = LAST_CLOSE_TIME.get(symbol)
        if last_close:
            elapsed = (datetime.now(timezone.utc) - last_close).total_seconds() / 60
            if elapsed < COOLDOWN_MINUTES:
                remaining = int(COOLDOWN_MINUTES - elapsed)
                print(f"  ⏱️  {symbol}: cooldown {remaining} min")
                logging.info(f"{symbol}: COOLDOWN {remaining} min")
                continue
        try:
            print(f"  Analizuje {symbol}...")
            data   = get_market_data(symbol)
            news   = get_news_context(symbol)
            social = get_social_context(symbol)
            # Oblicz wskaźniki techniczne
            ind = calculate_indicators(data["candles"])
            if ind.get("signals"):
                print(f"  📈 Wskaźniki: {', '.join(ind['signals'][:2])}")

            # Filtruj — pytaj Claude tylko gdy jest wyraźny sygnał
            rsi = ind.get("rsi", 50)
            macd_hist = ind.get("macd_hist", 0)
            # Relatywny próg MACD: 0.01% ceny (zamiast absolutnego 0.00001)
            price_ref  = data.get("bid", 1) or 1
            macd_thresh = price_ref * 0.0001
            has_signal = (
                rsi < 45 or rsi > 55 or
                abs(macd_hist) > macd_thresh or
                ind.get("error") is not None
            )

            if not has_signal:
                logging.info(f"{symbol}: SKIP — RSI={rsi:.1f} brak sygnału")
                continue

            decision = ask_claude(data, news, social, ind)

            action      = decision.get("action",      "WAIT")
            stop_loss   = decision.get("stop_loss",   0)
            take_profit = decision.get("take_profit", 0)
            confidence  = decision.get("confidence",  "LOW")
            reason      = decision.get("reason",      "")
            print(f"  {symbol}: {action} | {confidence} | {reason}")
            logging.info(f"{symbol}: {action} | SL={stop_loss} TP={take_profit} | Pewnosc: {confidence} | Powod: {reason}")
            if action in ("BUY", "SELL") and confidence == "HIGH":
                # Filtr trendu — nie handluj pod prąd
                trend = get_trend(data["candles"])
                if action == "BUY" and trend == "DOWN":
                    print(f"  {symbol}: BUY zablokowany — trend DOWN")
                    logging.info(f"{symbol}: BUY zablokowany przez trend filter (trend=DOWN)")
                    continue
                if action == "SELL" and trend == "UP":
                    print(f"  {symbol}: SELL zablokowany — trend UP")
                    logging.info(f"{symbol}: SELL zablokowany przez trend filter (trend=UP)")
                    continue
                if not capital.is_market_open(symbol):
                    print(f"  {symbol}: Rynek zamkniety")
                    continue
                size   = POSITION_SIZE.get(symbol, 1.0)
                result = capital.open_position(epic=symbol, direction=action, size=size, stop_loss=stop_loss, take_profit=take_profit)
                print(f"  Zlecenie wyslane: {result}")
                # Zarejestruj pozycję od razu w KNOWN_POSITIONS
                time.sleep(2)
                fresh = get_open_positions_details()
                for fp in fresh:
                    did = fp.get("position", {}).get("dealId", "")
                    fsym = fp.get("market", {}).get("epic", "")
                    if did and did not in KNOWN_POSITIONS and fsym == symbol:
                        KNOWN_POSITIONS[did] = {
                            "symbol":     fsym,
                            "direction":  fp.get("position", {}).get("direction", ""),
                            "size":       fp.get("position", {}).get("size", 0),
                            "open_level": fp.get("position", {}).get("openLevel", 0),
                            "pnl":        0,
                        }
                        logging.info(f"Zarejestrowano od razu: {fsym} {did}")
                        # Loguj otwarcie do trade_logger ze spreadem (PRIMARY)
                        try:
                            snap = capital.get_price(fsym).get("snapshot", {})
                            trade_logger.log_trade_open(
                                deal_id=did,
                                symbol=fsym,
                                direction=KNOWN_POSITIONS[did]["direction"],
                                size=float(KNOWN_POSITIONS[did]["size"] or 0),
                                bid=float(snap.get("bid", 0) or 0),
                                ask=float(snap.get("offer", 0) or 0),
                                open_level=float(KNOWN_POSITIONS[did]["open_level"] or 0),
                            )
                            logging.info(f"trade_logger: zalogowano otwarcie {fsym} {did}")
                        except Exception as log_err:
                            logging.warning(f"trade_logger open blad: {log_err}")
                if len(fresh) >= MAX_OPEN_POSITIONS:
                    print(f"  Max pozycji osiągnięte po otwarciu {symbol} — przerywam")
                    break
                try:
                    alert_trade_opened(symbol=symbol, action=action, size=size, stop_loss=stop_loss, take_profit=take_profit, reason=reason)
                    logging.info(f"alert_trade_opened OK: {symbol} {action}")
                except Exception as alert_err:
                    logging.error(f"alert_trade_opened FAIL dla {symbol}: {type(alert_err).__name__}: {alert_err}")
                    # Probuj jeszcze raz po 2s (typowy fix na rate limit / network glitch)
                    try:
                        time.sleep(2)
                        alert_trade_opened(symbol=symbol, action=action, size=size, stop_loss=stop_loss, take_profit=take_profit, reason=reason)
                        logging.info(f"alert_trade_opened OK po retry: {symbol} {action}")
                    except Exception as retry_err:
                        logging.error(f"alert_trade_opened RETRY FAIL dla {symbol}: {type(retry_err).__name__}: {retry_err}")
            elif action == "WAIT":
                print(f"  {symbol}: WAIT")
        except Exception as e:
            logging.error(f"Blad dla {symbol}: {e}")
            print(f"  Blad {symbol}: {e}")
        time.sleep(3)


def main():
    print("AI Trading Bot START")
    capital.login()
    print("Polaczono z Capital.com")
    load_cooldowns()  # Przywróć cooldowny po restarcie

    # Zarejestruj już otwarte pozycje (zabezpieczenie przed podwójnym otwarciem)
    try:
        existing = get_open_positions_details()
        for p in existing:
            did = p.get("position", {}).get("dealId", "")
            sym = p.get("market", {}).get("epic", "")
            dr  = p.get("position", {}).get("direction", "")
            sz  = p.get("position", {}).get("size", 0)
            if did:
                KNOWN_POSITIONS[did] = {"symbol": sym, "direction": dr, "size": sz}
        print(f"Znaleziono {len(existing)} istniejących pozycji")
        logging.info(f"Start: zarejestrowano {len(existing)} istniejących pozycji")
    except Exception as e:
        logging.warning(f"Błąd wczytywania pozycji na starcie: {e}")
    try:
        alert_bot_started(SYMBOLS)
    except Exception as e:
        logging.warning(f"Alert startowy nieudany: {e}")

    def daily_report():
        try:
            from reporter import parse_log
            from datetime import date
            events = parse_log(date.today())
            closed = [e for e in events if e.get("status") == "CLOSED"]
            wins     = sum(1 for e in closed if (e.get("pnl") or 0) > 0)
            losses   = sum(1 for e in closed if (e.get("pnl") or 0) <= 0)
            total_pnl = sum(e.get("pnl") or 0 for e in closed)
            win_rate  = (wins / len(closed) * 100) if closed else 0
            # Dodaj P&L z otwartych pozycji
            open_positions = get_open_positions_details()
            open_pnl = sum(p.get("position", {}).get("upl", 0) for p in open_positions)
            total_pnl_with_open = total_pnl + open_pnl
            generate_report()
            # Policz spready z trade_logger dla dzisiejszej daty
            try:
                from datetime import date as _d
                today_str = _d.today().isoformat()
                today_trades = trade_logger.get_trades_for_date(today_str)
                spread_total = sum(t.get("spread_cost", 0) or 0 for t in today_trades)
                spread_count = len(today_trades)
                spread_avg   = (spread_total / spread_count) if spread_count else 0
            except Exception as sp_err:
                logging.warning(f"Liczenie spreadow nieudane: {sp_err}")
                spread_total, spread_avg, spread_count = None, None, 0
            alert_daily_summary(total_pnl=total_pnl_with_open, wins=wins,
                                losses=losses, win_rate=win_rate,
                                spread_total=spread_total, spread_avg=spread_avg,
                                spread_count=spread_count)
        except Exception as e:
            logging.warning(f"Raport nieudany: {e}")

    schedule.every().day.at("21:59").do(daily_report)  # 21:59 UTC = 23:59 PL
    schedule.every(INTERVAL_MINUTES).minutes.do(run_cycle)
    schedule.every(1).hours.do(send_heartbeat)

    # Nie uruchamiaj od razu - poczekaj na pierwszy scheduled run
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("=== BOT ZATRZYMANY ===")
