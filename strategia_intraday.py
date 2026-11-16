"""Strategia intraday "trend + ritracciamento" su candele da 5 minuti.

Usata sia dal bot (bot_intraday.py, sul Raspberry) sia dal backtest
(backtest_intraday.py), così le regole sono identiche in entrambi.
Solo Python standard: niente pandas, per stare leggeri sul Raspberry.

Regole (solo acquisti):
  1. TREND: media veloce sopra la lenta, e la lenta sta salendo.
  2. RITRACCIAMENTO: in una delle ultime candele il minimo ha toccato la media veloce.
  3. RIPARTENZA: la candela appena chiusa è verde, chiude sopra la media veloce
     e sopra il massimo della candela prima  -> COMPRA.
  Stop loss: sotto il minimo del ritracciamento.  Take profit: RAPPORTO volte lo stop.
"""
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# ---- Parametri (modificabili) ----
TICKER = "SPY"
EMA_VELOCE = 9            # candele da 5 minuti
EMA_LENTA = 21
CANDELE_RITRACCIAMENTO = 3  # quante candele indietro cerco il tocco della media veloce
PENDENZA_CANDELE = 3      # la media lenta deve essere più alta di N candele fa
RAPPORTO = 2.0            # take profit = 2 x distanza dello stop
STOP_MIN_PCT = 0.0005     # stop troppo stretti (<0,05%) = rumore -> salto
STOP_MAX_PCT = 0.006      # stop troppo larghi (>0,6%) = rischio scomodo -> salto
MARGINE_STOP = 0.02       # $ sotto il minimo del ritracciamento

# Gestione del rischio
RISCHIO_PER_OPERAZIONE = 0.01   # 1% del conto se lo stop viene preso
LEVA_MAX = 1.0                  # 1 = mai più del capitale (niente leva)
MAX_OPERAZIONI_GIORNO = 3       # None = nessun limite
MAX_PERDITE_GIORNO = 2          # dopo 2 stop presi ci si ferma fino a domani
OBIETTIVO_GIORNO_PCT = 0.025    # +2,5% nel giorno (= 50 € su 2.000 €) -> ci si ferma

# Orari (ora di New York; in Italia +6 ore)
INIZIO = time(9, 45)            # salta i primi 15 minuti dopo l'apertura (15:45 in Italia)
ULTIMA_ENTRATA = time(15, 15)   # dopo non apre più nulla (21:15 in Italia)
CHIUSURA_FORZATA = time(15, 45) # chiude tutto (21:45 in Italia)


@dataclass
class Candela:
    t: datetime   # inizio candela, con fuso orario
    o: float
    h: float
    l: float
    c: float


@dataclass
class Segnale:
    entrata: float     # prezzo di riferimento (chiusura della candela)
    stop: float
    target: float

    @property
    def rischio(self) -> float:
        return self.entrata - self.stop


def ema(valori: list[float], periodo: int) -> list[float]:
    k = 2 / (periodo + 1)
    out: list[float] = []
    for v in valori:
        out.append(v if not out else v * k + out[-1] * (1 - k))
    return out


def ora_ny(c: Candela) -> time:
    """Ora di FINE candela a New York (la candela 9:40 finisce alle 9:45)."""
    t = c.t.astimezone(NY)
    m = t.hour * 60 + t.minute + 5
    return time(m // 60 % 24, m % 60)


def segnale(candele: list[Candela], medie: tuple[list[float], list[float]] | None = None,
            i: int | None = None) -> Segnale | None:
    """Controlla se la candela i (default: l'ultima) dà un segnale di acquisto."""
    if i is None:
        i = len(candele) - 1
    minimo = max(EMA_LENTA * 2, CANDELE_RITRACCIAMENTO + PENDENZA_CANDELE + 1)
    if i < minimo:
        return None
    if medie is None:
        chiusure = [c.c for c in candele[: i + 1]]
        veloce, lenta = ema(chiusure, EMA_VELOCE), ema(chiusure, EMA_LENTA)
    else:
        veloce, lenta = medie

    c, prima = candele[i], candele[i - 1]
    trend = veloce[i] > lenta[i] and lenta[i] > lenta[i - PENDENZA_CANDELE]
    if not trend:
        return None
    finestra = range(i - CANDELE_RITRACCIAMENTO, i)
    tocco = any(candele[j].l <= veloce[j] for j in finestra)
    ripartenza = c.c > c.o and c.c > veloce[i] and c.c > prima.h
    if not (tocco and ripartenza):
        return None

    stop = round(min(candele[j].l for j in range(i - CANDELE_RITRACCIAMENTO, i + 1))
                 - MARGINE_STOP, 2)
    distanza = c.c - stop
    if not (STOP_MIN_PCT * c.c <= distanza <= STOP_MAX_PCT * c.c):
        return None
    return Segnale(entrata=c.c, stop=stop, target=round(c.c + RAPPORTO * distanza, 2))


def quantita(capitale: float, prezzo: float, stop: float) -> int:
    """Azioni intere: rischio max RISCHIO_PER_OPERAZIONE, mai oltre LEVA_MAX x capitale."""
    rischio_azione = prezzo - stop
    if rischio_azione <= 0 or prezzo <= 0:
        return 0
    per_rischio = capitale * RISCHIO_PER_OPERAZIONE / rischio_azione
    per_capitale = capitale * LEVA_MAX / prezzo
    return int(min(per_rischio, per_capitale))


def puo_entrare(ora: time, operazioni_oggi: int, perdite_oggi: int,
                risultato_oggi_pct: float) -> str | None:
    """None se si può aprire una nuova operazione, altrimenti il motivo per non farlo."""
    if ora < INIZIO:
        return "troppo presto"
    if ora > ULTIMA_ENTRATA:
        return "troppo tardi per nuove entrate"
    if MAX_OPERAZIONI_GIORNO is not None and operazioni_oggi >= MAX_OPERAZIONI_GIORNO:
        return f"già {operazioni_oggi} operazioni oggi"
    if perdite_oggi >= MAX_PERDITE_GIORNO:
        return f"{perdite_oggi} perdite oggi: stop per la giornata"
    if risultato_oggi_pct >= OBIETTIVO_GIORNO_PCT:
        return "obiettivo del giorno raggiunto"
    return None
