"""Invio notifiche all'app (Expo push) e, facoltativo, a ntfy. Usa solo `requests`.

Variabili d'ambiente:
  EXPO_PUSH_TOKEN  codice push mostrato dall'app (più codici separati da virgola)
  NTFY_TOPIC       facoltativo, canale ntfy in alternativa all'app
  PROVA=1          stampa il messaggio senza inviarlo
"""
import os

import requests


def invia(titolo: str, testo: str, urgente: bool = False) -> None:
    if os.getenv("PROVA") == "1":
        print(f"[PROVA] {titolo}\n{testo}")
        return

    inviato = False
    token = [t.strip() for t in os.getenv("EXPO_PUSH_TOKEN", "").split(",") if t.strip()]
    if token:
        try:
            r = requests.post(
                "https://exp.host/--/api/v2/push/send",
                json=[{"to": t, "title": titolo, "body": testo, "sound": "default",
                       "priority": "high" if urgente else "default"} for t in token],
                timeout=20,
            )
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
        try:
            requests.post(f"https://ntfy.sh/{ntfy}", data=testo.encode("utf-8"),
                          headers={"Title": titolo.encode("utf-8"),
                                   "Priority": "high" if urgente else "default"},
                          timeout=20).raise_for_status()
            inviato = True
        except requests.RequestException as e:
            print(f"⚠️ ntfy non inviato: {e}")

    if not inviato:
        print(f"(Notifica non inviata: controlla EXPO_PUSH_TOKEN) {titolo} — {testo}")
        return
    print(f"Inviato: {titolo}")
