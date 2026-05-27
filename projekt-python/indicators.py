"""
indicators.py — Wskaźniki techniczne dla Trading Bota
RSI, MACD, Bollinger Bands, EMA obliczane z danych Capital.com
"""

import pandas as pd
import numpy as np
import logging

try:
    import ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False
    logging.warning("Biblioteka ta niedostępna — używam ręcznych obliczeń")


def _ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series, period=14):
    delta = series.diff()
    gain  = delta.where(delta > 0, 0.0)
    loss  = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period-1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period-1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_indicators(candles: list) -> dict:
    if len(candles) < 14:
        return {"error": "Za mało świec do analizy"}

    try:
        df = pd.DataFrame(candles)
        df = df.rename(columns={
            "open": "Open", "high": "High",
            "low":  "Low",  "close": "Close",
        })
        for col in ["Open", "High", "Low", "Close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Close"])

        close = df["Close"]
        high  = df["High"]
        low   = df["Low"]

        # RSI
        rsi_series = _rsi(close, 14)
        rsi_val    = round(float(rsi_series.iloc[-1]), 2)

        # MACD
        ema12      = _ema(close, 12)
        ema26      = _ema(close, 26)
        macd_line  = ema12 - ema26
        signal_line= _ema(macd_line, 9)
        macd_hist  = macd_line - signal_line
        macd_val   = round(float(macd_line.iloc[-1]),   5)
        signal_val = round(float(signal_line.iloc[-1]), 5)
        hist_val   = round(float(macd_hist.iloc[-1]),   5)

        # Bollinger Bands
        bb_mid   = close.rolling(20).mean()
        bb_std   = close.rolling(20).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_upper_val = round(float(bb_upper.iloc[-1]), 5)
        bb_mid_val   = round(float(bb_mid.iloc[-1]),   5)
        bb_lower_val = round(float(bb_lower.iloc[-1]), 5)

        # EMA 9 i 21
        ema9_val  = round(float(_ema(close, 9).iloc[-1]),  5)
        ema21_val = round(float(_ema(close, 21).iloc[-1]), 5)

        # ATR
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr_val = round(float(tr.ewm(span=14, adjust=False).mean().iloc[-1]), 5)

        current_close = float(close.iloc[-1])

        # Interpretacja sygnałów
        signals = []
        if rsi_val < 30:
            signals.append(f"RSI={rsi_val} OVERSOLD → BUY")
        elif rsi_val > 70:
            signals.append(f"RSI={rsi_val} OVERBOUGHT → SELL")
        else:
            signals.append(f"RSI={rsi_val} neutralny")

        if hist_val > 0 and macd_val > signal_val:
            signals.append("MACD bullish → BUY")
        elif hist_val < 0 and macd_val < signal_val:
            signals.append("MACD bearish → SELL")

        if current_close > bb_upper_val:
            signals.append("Cena > BB górny → SELL")
        elif current_close < bb_lower_val:
            signals.append("Cena < BB dolny → BUY")

        if ema9_val > ema21_val:
            signals.append("EMA9 > EMA21 → trend wzrostowy")
        else:
            signals.append("EMA9 < EMA21 → trend spadkowy")

        return {
            "rsi":         rsi_val,
            "macd":        macd_val,
            "macd_signal": signal_val,
            "macd_hist":   hist_val,
            "bb_upper":    bb_upper_val,
            "bb_mid":      bb_mid_val,
            "bb_lower":    bb_lower_val,
            "ema9":        ema9_val,
            "ema21":       ema21_val,
            "atr":         atr_val,
            "signals":     signals,
        }

    except Exception as e:
        logging.error(f"Błąd obliczania wskaźników: {e}")
        return {"error": str(e)}


if __name__ == "__main__":
    test_candles = [
        {"open": 1.10+i*0.001, "high": 1.11+i*0.001,
         "low": 1.09+i*0.001,  "close": 1.105+i*0.001,
         "volume": 100}
        for i in range(25)
    ]
    result = calculate_indicators(test_candles)
    print("Test wskaźników:")
    for k, v in result.items():
        print(f"  {k}: {v}")
