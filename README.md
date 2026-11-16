# Bot trading intraday (solo conto PAPER Alpaca)

`bot_intraday.py` gira di continuo sul **Raspberry di Butter** come servizio (`bot-trading`).
A borsa aperta, a ogni candela da 5 minuti di **SPY**, applica le regole di
`strategia_intraday.py`:

- **Trend**: media veloce (9) sopra la lenta (21), e la lenta sale
- **Ritracciamento**: il prezzo è tornato a toccare la media veloce
- **Ripartenza**: candela verde che chiude sopra la media e sopra il massimo precedente → **compra**
- Ogni acquisto parte con **stop loss** (sotto il minimo del ritracciamento) e **take profit**
  (2 volte lo stop), gestiti da Alpaca: restano attivi anche se il Raspberry si spegne
- Rischio 1% del conto a operazione, senza leva
- Limiti del giorno: max 3 operazioni, stop dopo 2 perdite, stop a +2,5%
- Opera dalle 15:45 alle 21:15 (ora italiana) e **alle 21:45 chiude tutto**

Tutti i parametri sono in cima a `strategia_intraday.py`.

## Installazione sul Raspberry

```bash
ssh butter.local
git clone https://github.com/mariololololo/segnalatore-trading ~/segnalatore-trading
bash ~/segnalatore-trading/raspberry/installa.sh
nano /etc/bot-trading.env          # chiavi Alpaca PAPER + codice push dell'app
bash ~/segnalatore-trading/raspberry/installa.sh   # di nuovo: prova a secco e avvio
journalctl -u bot-trading -f       # log in diretta
```

**Aggiornare l'app** (stato.json su GitHub): crea un token su GitHub
(*Settings → Developer settings → Fine-grained tokens*, solo questo repository,
permesso *Contents: Read and write*), poi sul Raspberry:

```bash
cd ~/segnalatore-trading
git config user.name "bot-raspberry"; git config user.email "bot@butter.local"
git config credential.helper store
git push        # utente: mariololololo · password: il token (viene ricordato)
```

Comandi utili: `sudo systemctl restart bot-trading` (dopo un `git pull`),
`sudo systemctl stop bot-trading` (ferma il bot).

## Backtest (sul PC)

```powershell
$env:ALPACA_KEY="PK..."; $env:ALPACA_SECRET="..."
python backtest_intraday.py                 # ultimi 12 mesi, conto da 2.000
python backtest_intraday.py --mesi 24
python backtest_intraday.py --leva 5        # come un CFD con leva 1:5
```

Confronta da solo i limiti di 3, 5 e nessuna operazione al giorno.
Prova del bot senza ordini: `$env:PROVA=1; python bot_intraday.py`.

> Strumento didattico, non consulenza finanziaria. I risultati passati non garantiscono
> quelli futuri.

---

## Versione precedente: bot serale (medie 5/20 giornaliere)

Non più attivo: il workflow di GitHub Actions è stato rimosso.

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
