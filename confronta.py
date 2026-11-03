"""Mette a confronto più strategie sugli stessi anni di dati.

Uso:  python confronta.py            (ultimi 10 anni di SPY)
      python confronta.py --anni 5
"""
import argparse

import pandas as pd

from strategia import (TICKER, Risultato, applica_rsi, applica_strategia,
                       scarica_prezzi)

COMMISSIONE = 0.001   # 0,1% per ogni acquisto o vendita (commissioni + spread, stima)
TASSE = 0.26          # tassazione italiana sulle plusvalenze

STRATEGIE = {
    "Lenta (medie 20/50)": lambda c: applica_strategia(c, 20, 50),
    "Veloce (medie 5/20)": lambda c: applica_strategia(c, 5, 20),
    "RSI (compra i cali)": applica_rsi,
}


def misura(chiusure: pd.Series, ris: Risultato) -> dict:
    rend_giorno = chiusure.pct_change().fillna(0)
    esposto = pd.Series(0.0, index=chiusure.index)
    for o in ris.operazioni:
        fine = o.data_vendita or chiusure.index[-1]
        esposto[(chiusure.index > o.data_acquisto) & (chiusure.index <= fine)] = 1.0
    curva = (1 + rend_giorno * esposto).cumprod()

    chiuse = [o for o in ris.operazioni if o.data_vendita is not None]
    n_scambi = 2 * len(chiuse) + (1 if ris.in_posizione else 0)
    lordo = curva.iloc[-1] - 1
    dopo_costi = curva.iloc[-1] * (1 - COMMISSIONE) ** n_scambi - 1
    anni = (chiusure.index[-1] - chiusure.index[0]).days / 365.25
    vinte = sum(o.rendimento > 0 for o in chiuse)
    return {
        "Operazioni": len(ris.operazioni),
        "Al mese": len(ris.operazioni) / (anni * 12),
        "Vincenti": vinte / len(chiuse) if chiuse else float("nan"),
        "Lordo": lordo,
        "Dopo costi": dopo_costi,
        "Costi pagati": lordo - dopo_costi,
        "Netto tasse": dopo_costi * (1 - TASSE) if dopo_costi > 0 else dopo_costi,
        "Peggior calo": float((curva / curva.cummax() - 1).min()),
        "Investito": esposto.mean(),
    }


def esegui(chiusure: pd.Series) -> None:
    righe = {nome: misura(chiusure, f(chiusure)) for nome, f in STRATEGIE.items()}
    ct = chiusure / chiusure.iloc[0]
    righe["Compra e tieni"] = {
        "Operazioni": 1, "Al mese": 0, "Vincenti": float("nan"),
        "Lordo": ct.iloc[-1] - 1,
        "Dopo costi": ct.iloc[-1] * (1 - COMMISSIONE) - 1,
        "Costi pagati": (ct.iloc[-1] - 1) - (ct.iloc[-1] * (1 - COMMISSIONE) - 1),
        "Netto tasse": (lambda r: r * (1 - TASSE) if r > 0 else r)(ct.iloc[-1] * (1 - COMMISSIONE) - 1),
        "Peggior calo": float((ct / ct.cummax() - 1).min()),
        "Investito": 1.0,
    }
    t = pd.DataFrame(righe).T
    formati = {"Operazioni": "{:.0f}", "Al mese": "{:.1f}", "Costi pagati": "{:.1%}"}
    for col in t.columns:
        f = formati.get(col, "{:+.1%}" if col not in ("Vincenti", "Investito") else "{:.0%}")
        t[col] = t[col].map(lambda v, f=f: "—" if pd.isna(v) else f.format(v))

    print(f"\n=== Confronto strategie {TICKER}: "
          f"{chiusure.index[0]:%d/%m/%Y} → {chiusure.index[-1]:%d/%m/%Y} ===\n")
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(t.to_string())
    print("\nLegenda:")
    print("  Al mese       operazioni medie al mese")
    print("  Vincenti      % di operazioni chiuse in guadagno")
    print("  Dopo costi    rendimento totale togliendo 0,1% a ogni acquisto e vendita")
    print("  Netto tasse   stima con tassazione 26% sul guadagno finale")
    print("  Peggior calo  la perdita più grande dal punto più alto (quanto avresti dovuto sopportare)")
    print("  Investito     % di giorni in cui eri dentro al mercato")
    print("\nI risultati passati non garantiscono quelli futuri.\n")


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
