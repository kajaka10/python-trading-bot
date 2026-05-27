"""
notifier.py — Alerty Telegram dla Trading Bota
"""

import os
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_message(text: str):
    """Wysyła wiadomość na Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        # Usuń wszystkie tagi HTML i markdown
        clean = text.replace("<b>", "").replace("</b>", "")
        clean = clean.replace("<i>", "").replace("</i>", "")
        clean = clean.replace("*", "").replace("_", "")
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text":    clean,
        }, timeout=10)
        r.raise_for_status()
    except Exception as e:
        logging.error(f"Telegram blad: {e}")


def alert_trade_opened(symbol, action, size, stop_loss, take_profit, reason):
    emoji = "🟢" if action == "BUY" else "🔴"
    msg = (
        f"{emoji} *NOWA POZYCJA*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📌 Instrument: *{symbol}*\n"
        f"📊 Kierunek:   *{action}*\n"
        f"📦 Rozmiar:    {size}\n"
        f"🛑 Stop Loss:  {stop_loss}\n"
        f"🎯 Take Profit:{take_profit}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🤖 AI: {reason}"
    )
    send_message(msg)


def alert_trade_closed(symbol, action, pnl, reason=""):
    """Alert o zamknięciu pozycji"""
    if pnl >= 0:
        emoji = "✅"
        pnl_str = f"+${pnl:.2f}"
    else:
        emoji = "❌"
        pnl_str = f"-${abs(pnl):.2f}"

    msg = (
        f"{emoji} <b>POZYCJA ZAMKNIĘTA</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📌 Instrument: <b>{symbol}</b>\n"
        f"📊 Kierunek:   <b>{action}</b>\n"
        f"💰 Wynik:      <b>{pnl_str}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📝 {reason}"
    )
    send_message(msg)


def alert_daily_summary(total_pnl, wins, losses, win_rate,
                        spread_total=None, spread_avg=None, spread_count=0):
    """Dzienny raport P&L. Linia o spreadach pojawia sie tylko gdy spread_count > 0."""
    emoji = "📈" if total_pnl >= 0 else "📉"
    pnl_str = f"+${total_pnl:.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):.2f}"

    spread_line = ""
    if spread_count and spread_total is not None and spread_avg is not None:
        spread_line = f"💸 Spready:     ${spread_total:.2f} (avg ${spread_avg:.2f}/trade)\n"

    msg = (
        f"{emoji} <b>RAPORT DZIENNY</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 Łączny P&L:  <b>{pnl_str}</b>\n"
        f"✅ Zyskowne:    {wins}\n"
        f"❌ Stratne:     {losses}\n"
        f"🎯 Win rate:    {win_rate:.1f}%\n"
        f"{spread_line}"
        f"━━━━━━━━━━━━━━━\n"
        f"📊 Raport Excel gotowy w folderze raporty/"
    )
    send_message(msg)


def alert_error(symbol, error):
    """Alert o błędzie"""
    msg = (
        f"⚠️ <b>BŁĄD BOTA</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📌 Instrument: {symbol}\n"
        f"❗ Błąd: {error}"
    )
    send_message(msg)


def alert_bot_started(symbols):
    """Alert o uruchomieniu bota"""
    msg = (
        f"🤖 <b>BOT URUCHOMIONY</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📋 Instrumenty: {', '.join(symbols)}\n"
        f"✅ Połączono z Capital.com\n"
        f"⏱️  Analiza co 15 minut"
    )
    send_message(msg)


if __name__ == "__main__":
    # Test
    send_message("✅ <b>Test połączenia z botem tradingowym!</b>\nJeśli widzisz tę wiadomość — wszystko działa.")

def send_document(file_path: str, caption: str = ""):
    """Wysyla plik (np. xlsx) na Telegram. Caption max 1024 znaki."""
    import os
    import requests
    if not os.path.exists(file_path):
        logging.warning(f"send_document: plik nie istnieje {file_path}")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
        with open(file_path, "rb") as f:
            files = {"document": f}
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption[:1024],
                "parse_mode": "HTML",
            }
            r = requests.post(url, data=data, files=files, timeout=30)
        if not r.ok:
            logging.warning(f"send_document blad: {r.status_code} {r.text[:200]}")
            return False
        return True
    except Exception as e:
        logging.warning(f"send_document wyjatek: {e}")
        return False

