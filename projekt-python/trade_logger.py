"""Logowanie transakcji ze spreadami do trades_log.json."""
import json
import os
from datetime import datetime, timezone
from threading import Lock

LOG_FILE = os.path.join(os.path.dirname(__file__), "trades_log.json")
_lock = Lock()


def log_trade_open(deal_id: str, symbol: str, direction: str,
                   size: float, bid: float, ask: float,
                   open_level: float = 0) -> None:
    """Zapisuje moment otwarcia pozycji ze spreadem."""
    if not deal_id:
        return
    with _lock:
        trades = _load()
        if deal_id in trades:
            return  # już zalogowana
        spread = abs(ask - bid) if (bid and ask) else 0
        trades[deal_id] = {
            "deal_id":      deal_id,
            "symbol":       symbol,
            "direction":    direction,
            "size":         size,
            "open_time":    datetime.now(timezone.utc).isoformat(),
            "open_bid":     bid,
            "open_ask":     ask,
            "open_level":   open_level,
            "spread_open":  round(spread, 5),
            "spread_cost":  round(spread * size, 2),
            "status":       "open",
        }
        _save(trades)


def log_trade_close(deal_id: str, bid: float, ask: float,
                    gross_pnl: float, symbol: str = "",
                    direction: str = "", size: float = 0) -> None:
    """Uzupełnia pozycję o dane zamknięcia i P&L.

    Jeśli wpisu otwarcia nie ma (pozycja sprzed wdrożenia loggera albo
    przegapione otwarcie), tworzymy minimalny wpis retroaktywny ze
    spread_cost=0 — w raporcie pojawi się jako 'retroaktywny'.
    """
    if not deal_id:
        return
    with _lock:
        trades = _load()
        t = trades.get(deal_id)

        if not t:
            # Wpis retroaktywny — brak danych otwarcia
            t = {
                "deal_id":      deal_id,
                "symbol":       symbol,
                "direction":    direction,
                "size":         size,
                "open_time":    "",
                "open_bid":     0,
                "open_ask":     0,
                "open_level":   0,
                "spread_open":  0,
                "spread_cost":  0,
                "retroactive":  True,
            }
            trades[deal_id] = t

        if t.get("status") == "closed":
            return

        t["close_time"]  = datetime.now(timezone.utc).isoformat()
        t["close_bid"]   = bid
        t["close_ask"]   = ask
        t["gross_pnl"]   = round(gross_pnl, 2)
        # Teoretyczny P&L = realny + spread (spread już zjadł część zysku)
        t["theoretical_pnl_no_spread"] = round(gross_pnl + t.get("spread_cost", 0), 2)
        t["status"]      = "closed"
        _save(trades)


def get_trades_for_date(date_str: str) -> list:
    """Zwraca listę zamkniętych pozycji z dnia (YYYY-MM-DD UTC)."""
    trades = _load()
    return [
        t for t in trades.values()
        if t.get("status") == "closed"
        and t.get("close_time", "").startswith(date_str)
    ]


def _load() -> dict:
    if not os.path.exists(LOG_FILE):
        return {}
    try:
        with open(LOG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(trades: dict) -> None:
    with open(LOG_FILE, "w") as f:
        json.dump(trades, f, indent=2)
