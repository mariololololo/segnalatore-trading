"""Controlla la strategia sull'ultima chiusura, salva stato.json e manda la notifica.

Variabili d'ambiente:
  EXPO_PUSH_TOKEN  codice push mostrato dall'app "Segnali SPY" (più codici separati da virgola)
  NTFY_TOPIC       facoltativo, canale ntfy in alternativa all'app
  PROVA=1          stampa il messaggio senza inviarlo
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from strategia import (MEDIA_LENTA, MEDIA_VELOCE, STOP_LOSS, TICKER,
                       applica_strategia, scarica_prezzi)

FILE_STATO = Path(__file__).with_name("stato.json")


def invia(titolo: str, testo: str, urgente: bool = False) -> None:
    if os.getenv("PROVA") == "1":
        print(f"[PROVA] {titolo}\n{testo}")
        return

    inviato = False
    token = [t.strip() for t in os.getenv("EXPO_PUSH_TOKEN", "").split(",") if t.strip()]
    if token:
        r = requests.post(
            "https://exp.host/--/api/v2/push/send",
            json=[{"to": t, "title": titolo, "body": testo, "sound": "default",
                   "priority": "high" if urgente else "default"} for t in token],
            timeout=20,
        )
        try:
            r.raise_for_status()
            errori = [t for t in r.json().get("data", []) if t.get("status") != "ok"]
        except (requests.RequestException, ValueError) as e:
            errori = [str(e)]
        if errori:
            print(f"⚠️ Notifica Expo non inviata: {errori}")
        else:
            inviato = True

    ntfy = os.getenv("NTFY_TOPIC")
    if ntfy:
        requests.post(f"https://ntfy.sh/{ntfy}", data=testo.encode("utf-8"),
                      headers={"Title": titolo.encode("utf-8"),
                               "Priority": "high" if urgente else "default"},
                      timeout=20).raise_for_status()
        inviato = True

    if not inviato:
        print(f"(Notifica non inviata: controlla il secret EXPO_PUSH_TOKEN) {titolo} — {testo}")
        return
    print(f"Inviato: {titolo}")


def salva_stato(chiusure, ris) -> None:
    sma_v = chiusure.rolling(MEDIA_VELOCE).mean().iloc[-1]
    sma_l = chiusure.rolling(MEDIA_LENTA).mean().iloc[-1]
    prezzo = float(chiusure.iloc[-1])
    stato = {
        "aggiornato": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ticker": TICKER,
        "data": chiusure.index[-1].date().isoformat(),
        "prezzo": round(prezzo, 2),
        "media_veloce": round(float(sma_v), 2),
        "media_lenta": round(float(sma_l), 2),
        "giorni_veloce": MEDIA_VELOCE,
        "giorni_lenta": MEDIA_LENTA,
        "in_posizione": ris.in_posizione,
    }
    if ris.in_posizione:
        op = ris.operazioni[-1]
        stato |= {
            "data_acquisto": op.data_acquisto.date().isoformat(),
            "prezzo_acquisto": round(op.prezzo_acquisto, 2),
            "stop_loss": round(op.prezzo_acquisto * (1 - STOP_LOSS), 2),
            "rendimento_aperto": round(prezzo / op.prezzo_acquisto - 1, 4),
        }
    if ris.eventi:
        data, evento = list(ris.eventi.items())[-1]
        stato["ultimo_segnale"] = {
            "tipo": "ACQUISTO" if evento == "ACQUISTO" else "VENDITA",
            "data": data.date().isoformat(),
            "prezzo": round(float(chiusure.loc[data]), 2),
        }
        if evento != "ACQUISTO":
            stato["ultimo_segnale"]["motivo"] = ris.operazioni[-1].motivo_vendita.capitalize()
    FILE_STATO.write_text(json.dumps(stato, indent=2, ensure_ascii=False) + "\n")


def main() -> None:
    chiusure = scarica_prezzi(TICKER, anni=2)
    ris = applica_strategia(chiusure)
    salva_stato(chiusure, ris)

    ultima_data = chiusure.index[-1]
    prezzo = float(chiusure.iloc[-1])
    giorno = ultima_data.strftime("%d/%m/%Y")
    evento = ris.eventi.get(ultima_data)

    if evento == "ACQUISTO":
        stop = prezzo * (1 - STOP_LOSS)
        invia(f"🟢 ACQUISTO {TICKER} a {prezzo:.2f} $",
              f"Media {MEDIA_VELOCE} sopra media {MEDIA_LENTA} (chiusura {giorno}). "
              f"Stop-loss {stop:.2f} $. Rischia max 1-2% del conto demo.", urgente=True)
    elif evento and evento.startswith("VENDITA"):
        op = ris.operazioni[-1]
        invia(f"🔴 VENDITA {TICKER} a {prezzo:.2f} $",
              f"{op.motivo_vendita.capitalize()} (chiusura {giorno}). "
              f"Acquisto a {op.prezzo_acquisto:.2f} $: risultato {op.rendimento:+.2%}.",
              urgente=True)
    elif ultima_data.weekday() == 0 or os.getenv("PROVA") == "1":
        # Ogni lunedì un promemoria breve, così sai che il programma funziona
        if ris.in_posizione:
            op = ris.operazioni[-1]
            stato = (f"In posizione da {op.data_acquisto:%d/%m} "
                     f"({prezzo / op.prezzo_acquisto - 1:+.2%}).")
        else:
            stato = "Fuori dal mercato, in attesa del prossimo incrocio."
        invia(f"{TICKER} {prezzo:.2f} $ · nessun segnale", stato)
    else:
        print(f"{giorno}: nessun segnale, nessuna notifica.")


if __name__ == "__main__":
    main()
