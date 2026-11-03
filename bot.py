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

    def ordini_eseguiti(self, simbolo: str) -> list:
        return self._req("GET", "/v2/orders", params={
            "status": "closed", "symbols": simbolo, "limit": 200, "direction": "asc"}) or []

    def storico_capitale(self) -> dict:
        return self._req("GET", "/v2/account/portfolio/history",
                         params={"period": "1A", "timeframe": "1D"}) or {}


def costruisci_storico(api: Alpaca) -> tuple[list, list]:
    """Operazioni eseguite (con risultato delle vendite) e capitale giorno per giorno."""
    operazioni, ultimo_acquisto = [], None
    for o in api.ordini_eseguiti(TICKER):
        if o.get("status") != "filled" or not o.get("filled_avg_price"):
            continue
        prezzo, qta = float(o["filled_avg_price"]), float(o["filled_qty"])
        op = {"tipo": "ACQUISTO" if o["side"] == "buy" else "VENDITA",
              "data": o["filled_at"][:10], "quantita": qta, "prezzo": round(prezzo, 2)}
        if o["side"] == "buy":
            ultimo_acquisto = prezzo
        elif ultimo_acquisto:
            op["risultato_pct"] = round(prezzo / ultimo_acquisto - 1, 4)
            op["risultato_usd"] = round((prezzo - ultimo_acquisto) * qta, 2)
            ultimo_acquisto = None
        operazioni.append(op)

    capitale = []
    try:
        st = api.storico_capitale()
        for ts, eq in zip(st.get("timestamp") or [], st.get("equity") or []):
            if eq is None or float(eq) <= 0:   # giorni prima che il conto esistesse
                continue
            ts = ts / 1000 if ts > 10**11 else ts      # secondi o millisecondi
            giorno = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
            capitale.append({"data": giorno, "valore": round(float(eq), 2)})
    except (RuntimeError, ValueError, TypeError) as e:
        print(f"⚠️ Storico capitale non disponibile: {e}")
    return operazioni[-50:], capitale


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

    if azione:
        ultimo_segnale = {"tipo": azione[0], "data": data.date().isoformat(), "prezzo": round(prezzo, 2)}
        if azione[2]:
            ultimo_segnale["motivo"] = azione[2]

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
    operazioni, storico = costruisci_storico(api)
    stato["capitale_iniziale"] = precedente.get("capitale_iniziale") or (
        storico[0]["valore"] if storico else round(capitale, 2))
    stato["storico_capitale"] = storico
    stato["operazioni"] = operazioni
    if not PROVA:
        FILE_STATO.write_text(json.dumps(stato, indent=2, ensure_ascii=False) + "\n")
    else:
        print(json.dumps(stato, indent=2, ensure_ascii=False))

    # ---- Notifica ----
    if azione:
        tipo, quantita, motivo = azione
        if tipo == "ACQUISTO":
            invia(f"🟢 Bot: compro {quantita} {TICKER}",
                  f"Media {MEDIA_VELOCE} sopra media {MEDIA_LENTA} (chiusura {giorno}, {prezzo:.2f} $). "
                  f"Ordine in coda, eseguito all'apertura di domani. "
                  f"Stop-loss circa {prezzo * (1 - STOP_LOSS):.2f} $.", urgente=True)
        else:
            entrata = float(pos["avg_entry_price"])
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


if __name__ == "__main__":
    main()
