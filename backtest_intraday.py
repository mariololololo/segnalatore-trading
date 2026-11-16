"""Backtest della strategia intraday su candele da 5 minuti di SPY.

Scarica lo storico da Alpaca (gratis, servono le stesse chiavi PAPER del bot)
e lo salva in cache in dati_5min_SPY.csv, così le prove successive sono istantanee.
Gira sul PC (sul Raspberry funziona, ma è lento).

  python backtest_intraday.py                       # ultimi 12 mesi, conto da 2.000
  python backtest_intraday.py --mesi 24 --capitale 2000
  python backtest_intraday.py --leva 5              # come un CFD con leva 1:5
  python backtest_intraday.py --file mie_candele.csv  # usa un CSV già scaricato (t,o,h,l,c)

Confronta da solo: max 3 operazioni al giorno, max 5, e senza limite.
"""
import argparse
import csv
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

import strategia_intraday as S

DATI_URL = "https://data.alpaca.markets/v2/stocks/{t}/bars"
SLITTAMENTO = 0.01   # $ per azione, a ogni entrata e uscita (prezzo un po' peggiore)


# ---------------------------------------------------------------- dati
def scarica(ticker: str, mesi: int, file: Path) -> list[S.Candela]:
    if file.exists():
        candele = leggi_csv(file)
        if candele and candele[0].t <= datetime.now(timezone.utc) - timedelta(days=mesi * 30 - 5):
            print(f"Uso la cache {file.name} ({len(candele)} candele)")
            return [c for c in candele
                    if c.t >= datetime.now(timezone.utc) - timedelta(days=mesi * 30)]
    key, secret = os.getenv("ALPACA_KEY", ""), os.getenv("ALPACA_SECRET", "")
    if not key or not secret:
        raise SystemExit("Imposta ALPACA_KEY e ALPACA_SECRET (le chiavi PAPER) per scaricare i dati.")
    s = requests.Session()
    s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
    inizio = datetime.now(timezone.utc) - timedelta(days=mesi * 30)
    fine = datetime.now(timezone.utc) - timedelta(minutes=20)   # piano gratuito: dati SIP > 15 min fa
    candele, pagina = [], None
    while True:
        params = {"timeframe": "5Min", "start": inizio.isoformat(), "end": fine.isoformat(),
                  "feed": "sip", "adjustment": "all", "limit": 10000}
        if pagina:
            params["page_token"] = pagina
        r = s.get(DATI_URL.format(t=ticker), params=params, timeout=60)
        r.raise_for_status()
        j = r.json()
        for b in j.get("bars") or []:
            candele.append(S.Candela(datetime.fromisoformat(b["t"].replace("Z", "+00:00")),
                                     b["o"], b["h"], b["l"], b["c"]))
        pagina = j.get("next_page_token")
        print(f"  scaricate {len(candele)} candele...", end="\r")
        if not pagina:
            break
    print()
    candele = [c for c in candele if S.time(9, 30) <= c.t.astimezone(S.NY).time() < S.time(16, 0)]
    scrivi_csv(file, candele)
    return candele


def leggi_csv(file: Path) -> list[S.Candela]:
    with file.open() as f:
        return [S.Candela(datetime.fromisoformat(r["t"]), float(r["o"]), float(r["h"]),
                          float(r["l"]), float(r["c"])) for r in csv.DictReader(f)]


def scrivi_csv(file: Path, candele: list[S.Candela]) -> None:
    with file.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "o", "h", "l", "c"])
        for c in candele:
            w.writerow([c.t.isoformat(), c.o, c.h, c.l, c.c])


# ---------------------------------------------------------------- simulazione
def simula(candele: list[S.Candela], capitale: float, max_op: int | None, leva: float) -> dict:
    S.MAX_OPERAZIONI_GIORNO, S.LEVA_MAX = max_op, leva
    chiusure = [c.c for c in candele]
    medie = (S.ema(chiusure, S.EMA_VELOCE), S.ema(chiusure, S.EMA_LENTA))

    conto, picco, max_dd = capitale, capitale, 0.0
    operazioni, giorni = [], {}
    giorno: date | None = None
    pos = None   # dict con qta, entrata, stop, target
    i = 0
    while i < len(candele):
        c = candele[i]
        oggi = c.t.astimezone(S.NY).date()
        ora = S.ora_ny(c)
        if oggi != giorno:
            giorno, inizio_giorno, op_oggi, perdite_oggi = oggi, conto, 0, 0

        # 1) gestisci la posizione aperta su questa candela
        if pos:
            uscita = None
            if c.l <= pos["stop"]:                       # prudente: se tocca entrambi, conta lo stop
                uscita = min(pos["stop"], c.o)           # se apre già sotto, esce all'apertura
            elif c.h >= pos["target"]:
                uscita = max(pos["target"], c.o)
            elif ora >= S.CHIUSURA_FORZATA or (i + 1 < len(candele) and
                                               candele[i + 1].t.astimezone(S.NY).date() != oggi):
                uscita = c.c
            if uscita is not None:
                uscita -= SLITTAMENTO
                utile = (uscita - pos["entrata"]) * pos["qta"]
                conto += utile
                operazioni.append(utile)
                if utile < 0:
                    perdite_oggi += 1
                pos = None
                picco = max(picco, conto)
                max_dd = max(max_dd, 1 - conto / picco)

        # 2) cerca un nuovo segnale (entrata all'apertura della candela successiva)
        if pos is None and i + 1 < len(candele):
            prossima = candele[i + 1]
            stesso_giorno = prossima.t.astimezone(S.NY).date() == oggi
            motivo = S.puo_entrare(ora, op_oggi, perdite_oggi, conto / inizio_giorno - 1)
            seg = S.segnale(candele, medie, i) if (stesso_giorno and motivo is None) else None
            if seg:
                entrata = prossima.o + SLITTAMENTO
                if entrata > seg.stop and entrata < seg.target:
                    qta = S.quantita(conto, entrata, seg.stop)
                    if qta >= 1:
                        pos = {"qta": qta, "entrata": entrata, "stop": seg.stop, "target": seg.target}
                        op_oggi += 1
        giorni[oggi] = conto / inizio_giorno - 1
        i += 1

    vinte = [u for u in operazioni if u > 0]
    perse = [u for u in operazioni if u <= 0]
    n_giorni = len(giorni)
    return {
        "operazioni": len(operazioni),
        "vinte_pct": len(vinte) / len(operazioni) if operazioni else 0,
        "media_vinta": sum(vinte) / len(vinte) if vinte else 0,
        "media_persa": sum(perse) / len(perse) if perse else 0,
        "rendimento": conto / capitale - 1,
        "finale": conto,
        "max_dd": max_dd,
        "giorni": n_giorni,
        "medio_giorno": (conto - capitale) / n_giorni if n_giorni else 0,
        "giorni_positivi": sum(1 for v in giorni.values() if v > 0) / n_giorni if n_giorni else 0,
        "giorni_50": sum(1 for v in giorni.values() if v >= 0.025) / n_giorni if n_giorni else 0,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mesi", type=int, default=12)
    p.add_argument("--capitale", type=float, default=2000)
    p.add_argument("--leva", type=float, default=1.0)
    p.add_argument("--file", type=Path)
    a = p.parse_args()

    file = a.file or Path(__file__).with_name(f"dati_5min_{S.TICKER}.csv")
    candele = leggi_csv(file) if a.file else scarica(S.TICKER, a.mesi, file)
    if not candele:
        raise SystemExit("Nessuna candela.")
    print(f"\n{S.TICKER} 5 minuti, dal {candele[0].t.date()} al {candele[-1].t.date()} · "
          f"capitale {a.capitale:,.0f} · leva max {a.leva:g} · rischio "
          f"{S.RISCHIO_PER_OPERAZIONE:.1%} a operazione · costi {SLITTAMENTO} $/azione per lato\n")
    print(f"{'Limite':<12}{'Oper.':>6}{'Vinte':>7}{'Media +':>9}{'Media -':>9}"
          f"{'Rend.':>8}{'Max DD':>8}{'€/giorno':>10}{'Gg +':>6}{'Gg ≥2,5%':>9}")
    for max_op in (3, 5, None):
        r = simula(candele, a.capitale, max_op, a.leva)
        nome = f"max {max_op}" if max_op else "nessuno"
        print(f"{nome:<12}{r['operazioni']:>6}{r['vinte_pct']:>7.0%}{r['media_vinta']:>9.2f}"
              f"{r['media_persa']:>9.2f}{r['rendimento']:>+8.1%}{-r['max_dd']:>8.1%}"
              f"{r['medio_giorno']:>+10.2f}{r['giorni_positivi']:>6.0%}{r['giorni_50']:>9.0%}")
    print("\nVinte = operazioni in guadagno · Max DD = peggior calo dal massimo · "
          "Gg + = giorni chiusi in positivo · Gg ≥2,5% = giorni con obiettivo raggiunto (50 € su 2.000)")
    print("Strumento didattico: i risultati passati non garantiscono quelli futuri.")


if __name__ == "__main__":
    main()
