"""Bot automatico: ogni sera decide e piazza gli ordini sul conto PAPER di Alpaca.

Regole (parametri in strategia.py):
  - COMPRA se non hai SPY e la media veloce incrocia sopra la lenta
    (e, se FILTRO_200 è attivo, il prezzo è sopra la media a 200 giorni)
  - VENDE tutto se la media veloce incrocia sotto la lenta,
    oppure se il prezzo è sceso del STOP_LOSS sotto il prezzo medio di acquisto

Gli ordini partono a borsa chiusa: Alpaca li mette in coda e li esegue
all'apertura del giorno dopo (15:30 ora italiana).

Variabili d'ambiente (Secrets di GitHub):
  ALPACA_KEY, ALPACA_SECRET   chiavi del conto paper (iniziano con PK)
  EXPO_PUSH_TOKEN             codice push dell'app (facoltativo)
  PROVA=1                     calcola e stampa, senza ordini né notifiche
"""
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from segnalatore import invia
from strategia import (FILTRO_200, MEDIA_LENTA, MEDIA_VELOCE, STOP_LOSS, TICKER,
                       scarica_prezzi, segnali_giorno)

# ---- Sicurezza ----
ALPACA_URL = "https://paper-api.alpaca.markets"   # SOLO conto paper, mai live
QUOTA_CONTO = 0.25    # usa il 25% del capitale per operazione → rischio ≈ 1% con stop al 4%

FILE_STATO = Path(__file__).with_name("stato.json")
PROVA = os.getenv("PROVA") == "1"


class Alpaca:
    def __init__(self) -> None:
        key, secret = os.getenv("ALPACA_KEY", ""), os.getenv("ALPACA_SECRET", "")
        if not key or not secret:
            raise SystemExit("Mancano ALPACA_KEY / ALPACA_SECRET nei Secrets di GitHub.")
        if not key.startswith("PK"):
            raise SystemExit("La chiave non è di un conto PAPER (deve iniziare con PK). Mi fermo.")
        self.s = requests.Session()
        self.s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})

    def _req(self, metodo: str, percorso: str, **kw):
        r = self.s.request(metodo, ALPACA_URL + percorso, timeout=20, **kw)
        if r.status_code == 404:
            return None
        if not r.ok:
            raise RuntimeError(f"Alpaca {metodo} {percorso}: {r.status_code} {r.text}")
        return r.json() if r.content else {}

    def conto(self) -> dict:
        return self._req("GET", "/v2/account")

    def posizione(self, simbolo: str) -> dict | None:
        return self._req("GET", f"/v2/positions/{simbolo}")

    def ordini_aperti(self, simbolo: str) -> list:
        return self._req("GET", "/v2/orders", params={"status": "open", "symbols": simbolo}) or []

    def compra(self, simbolo: str, quantita: int) -> dict:
        return self._req("POST", "/v2/orders", json={
            "symbol": simbolo, "qty": str(quantita), "side": "buy",
            "type": "market", "time_in_force": "day"})

    def vendi_tutto(self, simbolo: str) -> dict:
        return self._req("DELETE", f"/v2/positions/{simbolo}")


def leggi_stato_precedente() -> dict:
    try:
        return json.loads(FILE_STATO.read_text())
    except (OSError, ValueError):
        return {}


def main() -> None:
    chiusure = scarica_prezzi(TICKER, anni=2)
    seg = segnali_giorno(chiusure).iloc[-1]
    prezzo = float(chiusure.iloc[-1])
    data = chiusure.index[-1]
    giorno = data.strftime("%d/%m/%Y")

    api = Alpaca()
    conto = api.conto()
    capitale = float(conto["equity"])
    pos = api.posizione(TICKER)
    in_attesa = api.ordini_aperti(TICKER)
    precedente = leggi_stato_precedente()
    ultimo_segnale = precedente.get("ultimo_segnale")
    azione = None

    print(f"{giorno} {TICKER} {prezzo:.2f} $ | capitale {capitale:,.0f} $ | "
          f"posizione: {pos['qty'] if pos else 0} | incrocio su={seg.incrocio_su} "
          f"giu={seg.incrocio_giu} trend_ok={seg.trend_ok}")

    if in_attesa:
        print(f"C'è già un ordine in attesa ({len(in_attesa)}): non faccio nulla.")
    elif pos is None:
        if seg.incrocio_su and seg.trend_ok:
            quantita = math.floor(capitale * QUOTA_CONTO / prezzo)
            if quantita < 1:
                print("Capitale insufficiente per 1 azione.")
            else:
                azione = ("ACQUISTO", quantita, None)
                if not PROVA:
                    api.compra(TICKER, quantita)
    else:
        entrata = float(pos["avg_entry_price"])
        if prezzo <= entrata * (1 - STOP_LOSS):
            motivo = "Stop-loss"
        elif seg.incrocio_giu:
            motivo = "Incrocio al ribasso"
        else:
            motivo = None
        if motivo:
            azione = ("VENDITA", int(float(pos["qty"])), motivo)
            if not PROVA:
                api.vendi_tutto(TICKER)

    # ---- Notifica ----
    if azione:
        tipo, quantita, motivo = azione
        ultimo_segnale = {"tipo": tipo, "data": data.date().isoformat(), "prezzo": round(prezzo, 2)}
        if tipo == "ACQUISTO":
            invia(f"🟢 Bot: compro {quantita} {TICKER}",
                  f"Media {MEDIA_VELOCE} sopra media {MEDIA_LENTA} (chiusura {giorno}, {prezzo:.2f} $). "
                  f"Ordine in coda, eseguito all'apertura di domani. "
                  f"Stop-loss circa {prezzo * (1 - STOP_LOSS):.2f} $.", urgente=True)
        else:
            entrata = float(pos["avg_entry_price"])
            ultimo_segnale["motivo"] = motivo
            invia(f"🔴 Bot: vendo {quantita} {TICKER}",
                  f"{motivo} (chiusura {giorno}, {prezzo:.2f} $). Acquisto a {entrata:.2f} $: "
                  f"circa {prezzo / entrata - 1:+.2%}. Ordine eseguito all'apertura di domani.",
                  urgente=True)
    elif data.weekday() == 0 or PROVA:
        if pos:
            stato = (f"Ho {pos['qty']} {TICKER} ({float(pos['unrealized_plpc']):+.2%}). "
                     f"Capitale {capitale:,.0f} $.")
        else:
            stato = f"Nessuna posizione, aspetto il prossimo segnale. Capitale {capitale:,.0f} $."
        invia(f"Bot {TICKER} · nessuna operazione", stato)

    # ---- stato.json per l'app ----
    stato = {
        "aggiornato": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ticker": TICKER,
        "data": data.date().isoformat(),
        "prezzo": round(prezzo, 2),
        "media_veloce": round(float(chiusure.rolling(MEDIA_VELOCE).mean().iloc[-1]), 2),
        "media_lenta": round(float(chiusure.rolling(MEDIA_LENTA).mean().iloc[-1]), 2),
        "giorni_veloce": MEDIA_VELOCE,
        "giorni_lenta": MEDIA_LENTA,
        "filtro_200": FILTRO_200,
        "capitale": round(capitale, 2),
        "in_posizione": pos is not None,
    }
    if pos:
        entrata = float(pos["avg_entry_price"])
        stato |= {
            "quantita": float(pos["qty"]),
            "prezzo_acquisto": round(entrata, 2),
            "stop_loss": round(entrata * (1 - STOP_LOSS), 2),
            "rendimento_aperto": round(prezzo / entrata - 1, 4),
            "data_acquisto": precedente.get("data_acquisto")
            or (ultimo_segnale or {}).get("data"),
        }
    if ultimo_segnale:
        stato["ultimo_segnale"] = ultimo_segnale
    if not PROVA:
        FILE_STATO.write_text(json.dumps(stato, indent=2, ensure_ascii=False) + "\n")
    else:
        print(json.dumps(stato, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
