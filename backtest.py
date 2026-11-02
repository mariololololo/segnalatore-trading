"""Prova la strategia sui dati storici e la confronta con "compra e tieni".

Uso:  python backtest.py            (ultimi 10 anni di SPY)
      python backtest.py --anni 5
"""
import argparse

import pandas as pd

from strategia import TICKER, applica_strategia, scarica_prezzi

COMMISSIONE = 0.001   # 0,1% a operazione (acquisto e vendita), stima prudente


def max_drawdown(curva: pd.Series) -> float:
    return float((curva / curva.cummax() - 1).min())


def esegui(chiusure: pd.Series) -> None:
    ris = applica_strategia(chiusure)
    chiuse = [o for o in ris.operazioni if o.data_vendita is not None]

    # Curva del capitale: investito quando in posizione, liquido altrimenti
    rend_giorno = chiusure.pct_change().fillna(0)
    esposto = pd.Series(0.0, index=chiusure.index)
    for o in ris.operazioni:
        fine = o.data_vendita or chiusure.index[-1]
        esposto[(chiusure.index > o.data_acquisto) & (chiusure.index <= fine)] = 1.0
    curva = (1 + rend_giorno * esposto).cumprod()
    curva *= (1 - COMMISSIONE) ** (2 * len(chiuse) + (1 if ris.in_posizione else 0))
    compra_tieni = chiusure / chiusure.iloc[0]

    vinte = [o for o in chiuse if o.rendimento > 0]
    stop = [o for o in chiuse if o.motivo_vendita == "STOP-LOSS"]

    print(f"\n=== Backtest {TICKER}: {chiusure.index[0]:%d/%m/%Y} → {chiusure.index[-1]:%d/%m/%Y} ===\n")
    print(f"{'Acquisto':<12}{'Prezzo':>9}  {'Vendita':<12}{'Prezzo':>9}  {'Risultato':>9}  Motivo")
    for o in ris.operazioni:
        if o.data_vendita is None:
            print(f"{o.data_acquisto:%d/%m/%Y}  {o.prezzo_acquisto:9.2f}  (ancora aperta)")
        else:
            print(f"{o.data_acquisto:%d/%m/%Y}  {o.prezzo_acquisto:9.2f}  "
                  f"{o.data_vendita:%d/%m/%Y}  {o.prezzo_vendita:9.2f}  "
                  f"{o.rendimento:+9.2%}  {o.motivo_vendita}")

    print("\n--- Riepilogo ---")
    print(f"Operazioni chiuse:        {len(chiuse)}")
    if chiuse:
        print(f"Operazioni in guadagno:   {len(vinte)} ({len(vinte) / len(chiuse):.0%})")
        print(f"Chiuse con stop-loss:     {len(stop)}")
        print(f"Guadagno medio/perdita media: "
              f"{pd.Series([o.rendimento for o in vinte]).mean() if vinte else 0:+.2%} / "
              f"{pd.Series([o.rendimento for o in chiuse if o.rendimento <= 0]).mean() if len(vinte) < len(chiuse) else 0:+.2%}")
    print(f"Rendimento strategia:     {curva.iloc[-1] - 1:+.1%}   (peggior calo {max_drawdown(curva):.1%})")
    print(f"Rendimento compra&tieni:  {compra_tieni.iloc[-1] - 1:+.1%}   (peggior calo {max_drawdown(compra_tieni):.1%})")
    print(f"Tempo investito:          {esposto.mean():.0%} dei giorni")
    print("\nNota: commissioni stimate allo 0,1%, tasse (26%) non incluse. "
          "I risultati passati non garantiscono quelli futuri.\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--anni", type=int, default=10)
    p.add_argument("--csv", help="file CSV con colonne Date,Close (per test offline)")
    a = p.parse_args()
    if a.csv:
        df = pd.read_csv(a.csv, parse_dates=["Date"], index_col="Date")
        esegui(df["Close"])
    else:
        esegui(scarica_prezzi(TICKER, a.anni))
