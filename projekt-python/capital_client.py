"""
capital_client.py — Obsługa połączenia z Capital.com API
"""

import os
import pyotp
import requests
import logging
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"

class CapitalClient:
    def __init__(self):
        self.api_key   = os.getenv("CAPITAL_API_KEY")
        self.email     = os.getenv("CAPITAL_EMAIL")
        self.password  = os.getenv("CAPITAL_PASSWORD")
        self.totp      = pyotp.TOTP(os.getenv("CAPITAL_TOTP_SECRET"))
        self.cst       = None
        self.sec_token = None

    def login(self):
        """Logowanie i pobranie tokenów sesji"""
        r = requests.post(
            f"{BASE_URL}/session",
            headers={
                "X-CAP-API-KEY": self.api_key,
                "Content-Type": "application/json"
            },
            json={
                "identifier": self.email,
                "password": self.password,
                "encryptedPassword": False
            }
        )
        r.raise_for_status()
        self.cst       = r.headers.get("CST")
        self.sec_token = r.headers.get("X-SECURITY-TOKEN")
        logging.info("Capital.com: zalogowano pomyślnie")

    def _headers(self):
        """Nagłówki z tokenami sesji"""
        return {
            "X-CAP-API-KEY":    self.api_key,
            "CST":              self.cst,
            "X-SECURITY-TOKEN": self.sec_token,
            "Content-Type":     "application/json"
        }

    def get_candles(self, epic, resolution="MINUTE_15", max=50):
        """Pobiera świece dla danego instrumentu"""
        r = requests.get(
            f"{BASE_URL}/prices/{epic}",
            headers=self._headers(),
            params={"resolution": resolution, "max": max}
        )
        r.raise_for_status()
        return r.json()

    def get_price(self, epic):
        """Pobiera aktualną cenę instrumentu"""
        r = requests.get(
            f"{BASE_URL}/markets/{epic}",
            headers=self._headers()
        )
        r.raise_for_status()
        return r.json()

    def open_position(self, epic, direction, size, stop_loss, take_profit):
        """
        Otwiera pozycję CFD
        direction: 'BUY' lub 'SELL'
        size: wielkość pozycji (np. 1.0)
        stop_loss / take_profit: wartości cenowe
        """
        body = {
            "epic":           epic,
            "direction":      direction,
            "size":           size,
            "guaranteedStop": False,
            "stopLevel":      stop_loss,
            "profitLevel":    take_profit,
        }
        r = requests.post(
            f"{BASE_URL}/positions",
            headers=self._headers(),
            json=body
        )
        if not r.ok:
            print(f"  ⚠️  Błąd API: {r.status_code} — {r.text}")
            r.raise_for_status()
        logging.info(f"Zlecenie {direction} {epic} — odpowiedź: {r.json()}")
        return r.json()

    def get_positions(self):
        """Pobiera otwarte pozycje"""
        r = requests.get(
            f"{BASE_URL}/positions",
            headers=self._headers()
        )
        r.raise_for_status()
        return r.json()

    def close_position(self, deal_id: str):
        """Zamyka pozycję po deal_id.
        Próbuje najpierw DELETE /positions/{deal_id},
        jeśli nie zadziała — fallback: otwiera pozycję odwrotną.
        """
        # Spróbuj DELETE endpoint (oficjalny sposób zamknięcia)
        r = requests.delete(
            f"{BASE_URL}/positions/{deal_id}",
            headers=self._headers()
        )
        if r.ok:
            logging.info(f"Zamknieto pozycję DELETE: {deal_id}")
            return r.json()

        logging.warning(f"DELETE nie zadziałał ({r.status_code}), używam fallback POST")

        # Fallback: znajdź szczegóły i otwórz pozycję odwrotną
        positions = self.get_positions()
        position  = None
        for p in positions.get("positions", []):
            if p.get("position", {}).get("dealId") == deal_id:
                position = p
                break

        if not position:
            raise Exception(f"Nie znaleziono pozycji: {deal_id}")

        epic      = position.get("market",   {}).get("epic",      "")
        direction = position.get("position", {}).get("direction", "")
        size      = position.get("position", {}).get("size",       0)
        close_direction = "SELL" if direction == "BUY" else "BUY"

        r2 = requests.post(
            f"{BASE_URL}/positions",
            headers=self._headers(),
            json={
                "epic":           epic,
                "direction":      close_direction,
                "size":           size,
                "guaranteedStop": False,
            }
        )
        if not r2.ok:
            print(f"Błąd zamykania fallback: {r2.text}")
            r2.raise_for_status()
        logging.info(f"Zamknięto pozycję fallback POST: {deal_id}")
        return r2.json()

    def is_market_open(self, epic: str) -> bool:
        """Sprawdza czy rynek jest aktualnie otwarty"""
        try:
            info = self.get_price(epic)
            status = info.get("snapshot", {}).get("marketStatus", "")
            return status == "TRADEABLE"
        except Exception:
            return False

    def keep_alive(self):
        """Odświeża sesję"""
        try:
            r = requests.get(
                f"{BASE_URL}/session",
                headers=self._headers()
            )
            if r.status_code in (401, 403):
                logging.warning("Sesja wygasła — ponowne logowanie")
                self.login()
        except Exception:
            logging.warning("Ping nieudany — ponowne logowanie")
            self.login()
