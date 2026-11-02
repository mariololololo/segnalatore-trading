"""Regole della strategia: incrocio medie mobili 20/50 + stop-loss.

Usato sia dal segnalatore (ogni sera) sia dal backtest, così le regole
sono identiche in entrambi.
"""
from dataclasses import dataclass, field

import pandas as pd

# ---- Parametri (modificabili) ----
TICKER = "SPY"          # ETF S&P 500 in dollari
MEDIA_VELOCE = 20       # giorni
MEDIA_LENTA = 50        # giorni
STOP_LOSS = 0.04        # 4% sotto il prezzo di acquisto


@dataclass
class Operazione:
    data_acquisto: pd.Timestamp
    prezzo_acquisto: float
    data_vendita: pd.Timestamp | None = None
    prezzo_vendita: float | None = None
    motivo_vendita: str | None = None

    @property
    def rendimento(self) -> float | None:
        if self.prezzo_vendita is None:
            return None
        return self.prezzo_vendita / self.prezzo_acquisto - 1


@dataclass
class Risultato:
    operazioni: list[Operazione] = field(default_factory=list)
    eventi: dict = field(default_factory=dict)   # data -> testo evento

    @property
    def in_posizione(self) -> bool:
        return bool(self.operazioni) and self.operazioni[-1].data_vendita is None


def scarica_prezzi(ticker: str = TICKER, anni: int = 10) -> pd.Series:
    """Prezzi di chiusura giornalieri da Yahoo Finance."""
    import yfinance as yf

    dati = yf.download(ticker, period=f"{anni}y", interval="1d",
                       auto_adjust=True, progress=False)
    if dati.empty:
        raise RuntimeError(f"Nessun dato scaricato per {ticker}")
    chiusure = dati["Close"]
    if isinstance(chiusure, pd.DataFrame):   # yfinance recenti: colonne multi-livello
        chiusure = chiusure.iloc[:, 0]
    return chiusure.dropna()


def applica_strategia(chiusure: pd.Series,
                      veloce: int = MEDIA_VELOCE,
                      lenta: int = MEDIA_LENTA,
                      stop: float = STOP_LOSS) -> Risultato:
    """Ripercorre i prezzi giorno per giorno applicando le regole."""
    sma_v = chiusure.rolling(veloce).mean()
    sma_l = chiusure.rolling(lenta).mean()
    sopra = sma_v > sma_l

    ris = Risultato()
    for i in range(lenta, len(chiusure)):
        data = chiusure.index[i]
        prezzo = float(chiusure.iloc[i])
        incrocio_su = bool(sopra.iloc[i]) and not bool(sopra.iloc[i - 1])
        incrocio_giu = not bool(sopra.iloc[i]) and bool(sopra.iloc[i - 1])

        if not ris.in_posizione:
            if incrocio_su:
                ris.operazioni.append(Operazione(data, prezzo))
                ris.eventi[data] = "ACQUISTO"
        else:
            op = ris.operazioni[-1]
            if prezzo <= op.prezzo_acquisto * (1 - stop):
                motivo = "STOP-LOSS"
            elif incrocio_giu:
                motivo = "INCROCIO AL RIBASSO"
            else:
                continue
            op.data_vendita, op.prezzo_vendita, op.motivo_vendita = data, prezzo, motivo
            ris.eventi[data] = f"VENDITA ({motivo})"
    return ris
