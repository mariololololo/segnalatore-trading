"""Bot intraday: gira di continuo sul Raspberry e opera sul conto PAPER di Alpaca.

A borsa aperta, a ogni candela da 5 minuti chiusa:
  - se c'è un segnale (regole in strategia_intraday.py) compra con un ordine "bracket":
    stop loss e take profit partono insieme all'acquisto, li gestisce Alpaca
    (quindi la posizione resta protetta anche se il Raspberry si spegne);
  - rispetta i limiti del giorno (operazioni, perdite, obiettivo);
  - 15 minuti prima della chiusura vende tutto: niente posizioni la notte.
Aggiorna stato.json per l'app e, se GIT_PUSH=1, lo carica su GitHub.

Variabili d'ambiente (file /etc/bot-trading.env sul Raspberry):
  ALPACA_KEY, ALPACA_SECRET   chiavi del conto paper (iniziano con PK)
  EXPO_PUSH_TOKEN             codice push dell'app (facoltativo)
  GIT_PUSH=1                  carica stato.json su GitHub (serve l'accesso git configurato)
  PROVA=1                     fa un solo giro: calcola e stampa, senza ordini né notifiche
"""
import json
import os
import subprocess
import sys
import time as orologio
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import strategia_intraday as S
from notifiche import invia

ALPACA_URL = "https://paper-api.alpaca.markets"   # SOLO conto paper, mai live
DATI_URL = "https://data.alpaca.markets/v2/stocks/{t}/bars"
CARTELLA = Path(__file__).parent
FILE_STATO = CARTELLA / "stato.json"
FILE_GIORNO = CARTELLA / "giornata.json"          # contatori del giorno (non va su GitHub)
PROVA = os.getenv("PROVA") == "1"
GIT_PUSH = os.getenv("GIT_PUSH") == "1"
OGNI_QUANTO_PUSH = timedelta(minutes=30)


def log(msg: str) -> None:
    print(f"{datetime.now(S.NY):%Y-%m-%d %H:%M:%S} NY | {msg}", flush=True)


class Alpaca:
    def __init__(self) -> None:
        key, secret = os.getenv("ALPACA_KEY", ""), os.getenv("ALPACA_SECRET", "")
        if not key or not secret:
            sys.exit("Mancano ALPACA_KEY / ALPACA_SECRET.")
        if not key.startswith("PK"):
            sys.exit("La chiave non è di un conto PAPER (deve iniziare con PK). Mi fermo.")
        self.s = requests.Session()
        self.s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})

    def _req(self, metodo: str, url: str, **kw):
        r = self.s.request(metodo, url if url.startswith("http") else ALPACA_URL + url,
                           timeout=20, **kw)
        if r.status_code == 404:
            return None
        if not r.ok:
            raise RuntimeError(f"Alpaca {metodo} {url}: {r.status_code} {r.text}")
        return r.json() if r.content else {}

    def orologio(self) -> dict:
        return self._req("GET", "/v2/clock")

    def conto(self) -> dict:
        return self._req("GET", "/v2/account")

    def posizione(self) -> dict | None:
        return self._req("GET", f"/v2/positions/{S.TICKER}")

    def ordini_aperti(self) -> list:
        return self._req("GET", "/v2/orders", params={"status": "open", "symbols": S.TICKER,
                                                      "nested": "false"}) or []

    def candele(self) -> list[S.Candela]:
        """Candele da 5 minuti degli ultimi giorni (feed IEX, gratuito e in tempo reale)."""
        inizio = datetime.now(timezone.utc) - timedelta(days=5)
        j = self._req("GET", DATI_URL.format(t=S.TICKER), params={
            "timeframe": "5Min", "start": inizio.isoformat(), "feed": "iex",
            "limit": 10000, "sort": "asc"}) or {}
        out = []
        for b in j.get("bars") or []:
            t = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
            if S.time(9, 30) <= t.astimezone(S.NY).time() < S.time(16, 0):
                out.append(S.Candela(t, b["o"], b["h"], b["l"], b["c"]))
        adesso = datetime.now(timezone.utc)
        return [c for c in out if c.t + timedelta(minutes=5) <= adesso]   # solo candele chiuse

    def compra_bracket(self, qta: int, stop: float, target: float) -> dict:
        return self._req("POST", "/v2/orders", json={
            "symbol": S.TICKER, "qty": str(qta), "side": "buy", "type": "market",
            "time_in_force": "day", "order_class": "bracket",
            "take_profit": {"limit_price": f"{target:.2f}"},
            "stop_loss": {"stop_price": f"{stop:.2f}"}})

    def chiudi_tutto(self) -> None:
        self._req("DELETE", "/v2/orders")                         # cancella stop/target pendenti
        self._req("DELETE", f"/v2/positions/{S.TICKER}")

    def ordini_eseguiti(self) -> list:
        return self._req("GET", "/v2/orders", params={
            "status": "closed", "symbols": S.TICKER, "limit": 200, "direction": "desc",
            "nested": "false"}) or []

    def storico_capitale(self) -> dict:
        return self._req("GET", "/v2/account/portfolio/history",
                         params={"period": "1A", "timeframe": "1D"}) or {}


# ---------------------------------------------------------------- stato del giorno
def leggi_json(file: Path) -> dict:
    try:
        return json.loads(file.read_text())
    except (OSError, ValueError):
        return {}


def giornata(oggi: str) -> dict:
    g = leggi_json(FILE_GIORNO)
    if g.get("data") != oggi:
        g = {"data": oggi, "operazioni": 0, "perdite": 0, "aperta": None,
             "riepilogo_inviato": False, "eventi": []}
    return g


def salva_giornata(g: dict) -> None:
    if not PROVA:
        FILE_GIORNO.write_text(json.dumps(g, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- stato.json per l'app
def storico(api: Alpaca) -> tuple[list, list]:
    ordini = [o for o in api.ordini_eseguiti()
              if o.get("status") == "filled" and o.get("filled_avg_price")]
    ordini.sort(key=lambda o: o["filled_at"])
    operazioni, acquisto = [], None
    for o in ordini:
        prezzo, qta = float(o["filled_avg_price"]), float(o["filled_qty"])
        quando = datetime.fromisoformat(o["filled_at"].replace("Z", "+00:00")).astimezone(S.NY)
        op = {"tipo": "ACQUISTO" if o["side"] == "buy" else "VENDITA",
              "data": quando.date().isoformat(), "ora": quando.strftime("%H:%M") + " NY",
              "quantita": round(qta, 3), "prezzo": round(prezzo, 2)}
        if o["side"] == "buy":
            acquisto = prezzo
        elif acquisto:
            op["risultato_pct"] = round(prezzo / acquisto - 1, 4)
            op["risultato_usd"] = round((prezzo - acquisto) * qta, 2)
            acquisto = None
        operazioni.append(op)
    capitale = []
    try:
        st = api.storico_capitale()
        for ts, eq in zip(st.get("timestamp") or [], st.get("equity") or []):
            if eq is None or float(eq) <= 0:
                continue
            ts = ts / 1000 if ts > 10**11 else ts
            capitale.append({"data": datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
                             "valore": round(float(eq), 2)})
    except (RuntimeError, ValueError, TypeError) as e:
        log(f"Storico capitale non disponibile: {e}")
    return operazioni[-50:], capitale


def scrivi_stato(api: Alpaca, conto: dict, pos: dict | None, candele: list[S.Candela],
                 g: dict, nota: str) -> None:
    capitale = float(conto["equity"])
    chiusure = [c.c for c in candele]
    veloce = S.ema(chiusure, S.EMA_VELOCE)[-1] if chiusure else None
    lenta = S.ema(chiusure, S.EMA_LENTA)[-1] if chiusure else None
    precedente = leggi_json(FILE_STATO)
    operazioni, capitali = storico(api)
    stato = {
        "aggiornato": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strategia": "intraday 5 minuti: trend + ritracciamento",
        "ticker": S.TICKER,
        "data": candele[-1].t.astimezone(S.NY).date().isoformat() if candele else None,
        "prezzo": round(chiusure[-1], 2) if chiusure else None,
        "media_veloce": round(veloce, 2) if veloce else None,
        "media_lenta": round(lenta, 2) if lenta else None,
        "giorni_veloce": S.EMA_VELOCE,      # qui sono candele da 5 minuti, non giorni
        "giorni_lenta": S.EMA_LENTA,
        "capitale": round(capitale, 2),
        "risultato_oggi_pct": round(capitale / float(conto["last_equity"]) - 1, 4),
        "operazioni_oggi": g["operazioni"],
        "perdite_oggi": g["perdite"],
        "nota": nota,
        "in_posizione": pos is not None,
        "capitale_iniziale": precedente.get("capitale_iniziale") or (
            capitali[0]["valore"] if capitali else round(capitale, 2)),
        "storico_capitale": capitali,
        "operazioni": operazioni,
    }
    if pos:
        aperta = g.get("aperta") or {}
        entrata = float(pos["avg_entry_price"])
        stato |= {"quantita": round(float(pos["qty"]), 3), "prezzo_acquisto": round(entrata, 2),
                  "stop_loss": aperta.get("stop"), "take_profit": aperta.get("target"),
                  "rendimento_aperto": round(float(pos["unrealized_plpc"]), 4),
                  "data_acquisto": aperta.get("data")}
    if g["eventi"]:
        stato["ultimo_segnale"] = g["eventi"][-1]
    elif precedente.get("ultimo_segnale"):
        stato["ultimo_segnale"] = precedente["ultimo_segnale"]
    if PROVA:
        print(json.dumps({k: v for k, v in stato.items()
                          if k not in ("storico_capitale", "operazioni")}, indent=2))
    else:
        FILE_STATO.write_text(json.dumps(stato, indent=2, ensure_ascii=False) + "\n")


_ultimo_push = datetime.min.replace(tzinfo=timezone.utc)


def carica_su_github(subito: bool = False) -> None:
    global _ultimo_push
    adesso = datetime.now(timezone.utc)
    if PROVA or not GIT_PUSH or (not subito and adesso - _ultimo_push < OGNI_QUANTO_PUSH):
        return
    _ultimo_push = adesso
    git = ["git", "-C", str(CARTELLA)]
    try:
        subprocess.run(git + ["add", "stato.json"], check=True, capture_output=True)
        if subprocess.run(git + ["diff", "--cached", "--quiet"]).returncode == 0:
            return
        subprocess.run(git + ["commit", "-q", "-m", f"Stato {adesso:%Y-%m-%d %H:%M} UTC"],
                       check=True, capture_output=True)
        subprocess.run(git + ["pull", "-q", "--rebase", "--autostash"], check=True,
                       capture_output=True, timeout=60)
        subprocess.run(git + ["push", "-q"], check=True, capture_output=True, timeout=60)
        log("stato.json caricato su GitHub")
    except (subprocess.SubprocessError, OSError) as e:
        err = getattr(e, "stderr", b"") or b""
        log(f"⚠️ Push su GitHub non riuscito: {e} {err.decode(errors='ignore')[:200]}")


# ---------------------------------------------------------------- un giro
def evento(g: dict, tipo: str, prezzo: float, motivo: str | None = None) -> None:
    e = {"tipo": tipo, "data": datetime.now(S.NY).date().isoformat(),
         "ora": datetime.now(S.NY).strftime("%H:%M") + " NY", "prezzo": round(prezzo, 2)}
    if motivo:
        e["motivo"] = motivo
    g["eventi"].append(e)


def giro(api: Alpaca) -> None:
    adesso = datetime.now(S.NY)
    g = giornata(adesso.date().isoformat())
    clock = api.orologio()
    conto = api.conto()
    capitale, ieri = float(conto["equity"]), float(conto["last_equity"])
    risultato_oggi = capitale / ieri - 1
    pos = api.posizione()
    aperti = api.ordini_aperti()
    candele = api.candele()
    importante = False

    # 1) una posizione si è chiusa da sola (stop o target)?
    if g["aperta"] and pos is None and not aperti:
        a = g["aperta"]
        utile = capitale - a["capitale_entrata"]
        preso = "Take profit" if utile > 0 else "Stop loss"
        if utile < 0:
            g["perdite"] += 1
        evento(g, "VENDITA", candele[-1].c if candele else 0, preso)
        invia(f"{'🟢' if utile > 0 else '🔴'} Bot: {preso.lower()} su {S.TICKER} ({utile:+,.2f} $)",
              f"Oggi: {g['operazioni']} operazioni, {g['perdite']} perse, "
              f"{capitale / ieri - 1:+.2%} sul conto.", urgente=True)
        g["aperta"] = None
        importante = True

    nota = ""
    chiusura_borsa = datetime.fromisoformat(clock["next_close"]).astimezone(S.NY)
    ora_chiusura_forzata = min(
        adesso.replace(hour=S.CHIUSURA_FORZATA.hour, minute=S.CHIUSURA_FORZATA.minute,
                       second=0, microsecond=0),
        chiusura_borsa - timedelta(minutes=15))           # giorni con chiusura anticipata

    if not clock["is_open"]:
        nota = "borsa chiusa"
    elif adesso >= ora_chiusura_forzata:
        nota = "fine giornata: niente nuove operazioni"
        if pos or aperti:
            if not PROVA:
                api.chiudi_tutto()
            utile = float(pos["unrealized_pl"]) if pos else 0.0
            if pos and utile < 0:
                g["perdite"] += 1
            evento(g, "VENDITA", float(pos["current_price"]) if pos else 0, "Chiusura di fine giornata")
            invia(f"⏹ Bot: chiudo {S.TICKER} a fine giornata ({utile:+,.2f} $)",
                  "Niente posizioni aperte la notte.", urgente=True)
            g["aperta"], pos, importante = None, None, True
    elif pos and not [o for o in aperti if o.get("side") == "sell" or o.get("legs")]:
        # posizione senza stop: non deve succedere, per sicurezza chiudo
        if not PROVA:
            api.chiudi_tutto()
        invia(f"⚠️ Bot: posizione {S.TICKER} senza stop loss", "L'ho chiusa per sicurezza.",
              urgente=True)
        g["aperta"], pos, importante = None, None, True
    elif pos is None and not aperti and candele:
        ora_candela = S.ora_ny(candele[-1])
        oggi_candela = candele[-1].t.astimezone(S.NY).date() == adesso.date()
        motivo = S.puo_entrare(ora_candela, g["operazioni"], g["perdite"], risultato_oggi)
        seg = S.segnale(candele) if (oggi_candela and motivo is None) else None
        if motivo:
            nota = motivo
        elif not seg:
            nota = "nessun segnale, aspetto"
        else:
            qta = S.quantita(capitale, seg.entrata, seg.stop)
            if qta < 1:
                nota = "capitale insufficiente per 1 azione"
            else:
                log(f"SEGNALE: compro {qta} a ~{seg.entrata:.2f}, stop {seg.stop:.2f}, "
                    f"target {seg.target:.2f}")
                if not PROVA:
                    api.compra_bracket(qta, seg.stop, seg.target)
                g["operazioni"] += 1
                g["aperta"] = {"data": adesso.date().isoformat(), "qta": qta,
                               "entrata": seg.entrata, "stop": seg.stop, "target": seg.target,
                               "capitale_entrata": capitale}
                evento(g, "ACQUISTO", seg.entrata)
                rischio = qta * seg.rischio
                invia(f"🟢 Bot: compro {qta} {S.TICKER} a ~{seg.entrata:.2f} $",
                      f"Stop {seg.stop:.2f} (rischio ~{rischio:,.0f} $), "
                      f"target {seg.target:.2f} (~{rischio * S.RAPPORTO:,.0f} $). "
                      f"Operazione {g['operazioni']} di oggi.", urgente=True)
                nota, importante = "posizione aperta", True
    elif pos:
        nota = "posizione aperta, stop e target attivi"

    # 2) riepilogo una volta a fine giornata
    if not clock["is_open"] and not g["riepilogo_inviato"] and adesso.hour >= 16 \
            and adesso.weekday() < 5 and g["operazioni"]:
        invia(f"Bot {S.TICKER} · giornata chiusa {risultato_oggi:+.2%}",
              f"{g['operazioni']} operazioni, {g['perdite']} perse. "
              f"Capitale {capitale:,.0f} $ ({capitale - ieri:+,.0f} $ oggi).")
        g["riepilogo_inviato"], importante = True, True

    log(f"{S.TICKER} {candele[-1].c if candele else '-'} | capitale {capitale:,.0f} $ "
        f"({risultato_oggi:+.2%}) | op {g['operazioni']} perse {g['perdite']} | {nota}")
    salva_giornata(g)
    scrivi_stato(api, conto, pos, candele, g, nota)
    carica_su_github(subito=importante)


def attesa(api: Alpaca) -> float:
    """Secondi da aspettare: subito dopo la prossima candela, o fino all'apertura."""
    clock = api.orologio()
    adesso = datetime.now(timezone.utc)
    if not clock["is_open"]:
        apertura = datetime.fromisoformat(clock["next_open"])
        return max(60, min(3600, (apertura - adesso).total_seconds() - 60))
    secondi = adesso.minute % 5 * 60 + adesso.second
    return 5 * 60 - secondi + 20     # 20 s dopo la chiusura della candela


def main() -> None:
    api = Alpaca()
    if PROVA:
        giro(api)
        return
    invia(f"🤖 Bot {S.TICKER} avviato", "Strategia intraday 5 minuti, conto paper.")
    errori = 0
    while True:
        try:
            giro(api)
            errori = 0
            pausa = attesa(api)
        except Exception as e:   # noqa: BLE001 — il bot non deve mai fermarsi per un errore
            errori += 1
            log(f"ERRORE: {e}\n{traceback.format_exc()}")
            if errori == 3:
                invia("⚠️ Bot: errori ripetuti", str(e)[:200], urgente=True)
            pausa = 60
        orologio.sleep(pausa)


if __name__ == "__main__":
    main()
