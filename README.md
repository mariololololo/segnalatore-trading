# Bot trading (solo conto PAPER Alpaca)

`bot.py` gira ogni sera su GitHub Actions: legge i prezzi dello SPY, decide con le regole
di `strategia.py` (medie 5/20 + stop-loss 4%) e piazza da solo gli ordini sul conto
**paper** di Alpaca (Secrets `ALPACA_KEY` e `ALPACA_SECRET`). Investe il 95% del capitale
a operazione. Le chiavi devono iniziare con `PK`, altrimenti il bot si ferma.

Per provarlo senza ordini: `$env:PROVA=1; python bot.py` (servono le chiavi come variabili d'ambiente).

Confronto strategie: `python confronta.py`.

---

## Versione precedente: segnalatore

Ogni sera, dal lunedì al venerdì, dopo la chiusura di Wall Street, controlla lo **SPY**
con la strategia "incrocio medie mobili 20/50 + stop-loss 4%", aggiorna `stato.json`
e manda una notifica all'app **Segnali SPY** sull'iPhone:

- 🟢 **ACQUISTO**: la media a 20 giorni incrocia sopra quella a 50
- 🔴 **VENDITA**: la media a 20 incrocia sotto quella a 50, oppure il prezzo scende del 4% sotto l'acquisto
- ogni lunedì un breve promemoria dello stato, così sai che funziona

I segnali si eseguono a mano sul conto **Paper Trading di TradingView**.
Il programma non compra e non vende nulla da solo.

## Configurazione

1. Crea su GitHub un repository **pubblico** (serve perché l'app possa leggere
   `stato.json`; dentro non c'è nulla di personale, i codici stanno nei Secrets)
   e caricaci tutti i file di questa cartella, compresa `.github/workflows/segnali.yml`.
2. Installa l'app (vedi `segnali-app/LEGGIMI.md`), aprila e copia il **codice push**
   da *Configurazione notifiche*.
3. Nel repository: *Settings → Secrets and variables → Actions → New repository secret*,
   nome `EXPO_PUSH_TOKEN`, valore il codice copiato.
4. *Actions → Segnalatore serale → Run workflow*. Al primo giro viene creato
   `stato.json` e l'app mostra i dati (trascina giù per aggiornare).

Facoltativo: se vuoi le notifiche anche con ntfy, aggiungi il secret `NTFY_TOPIC`.

## Backtest (sul computer)

```bash
pip install -r requirements.txt
python backtest.py          # ultimi 10 anni
python backtest.py --anni 5
```

## Modificare la strategia

I parametri sono in cima a `strategia.py`: `TICKER`, `MEDIA_VELOCE`, `MEDIA_LENTA`, `STOP_LOSS`.
Dopo ogni modifica rilancia il backtest prima di fidarti dei nuovi segnali.

> Strumento didattico, non consulenza finanziaria. I risultati passati non garantiscono
> quelli futuri.
