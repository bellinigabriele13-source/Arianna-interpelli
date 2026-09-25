# Monitoraggio interpelli — Arianna

Controllo orario, indipendente da Claude: gira su GitHub Actions, quindi funziona
anche a computer spento. Ogni ora (8:00–20:00 italiane) rilegge le fonti, aggiorna
l'archivio e manda un'email ad Arianna solo se trova avvisi nuovi.

## Cosa c'è dentro
- `monitor.py` — lo scraper (invariato).
- `fonti_interpelli.json` — le 99 fonti.
- `runner.py` — tiene l'archivio e il registro, manda l'email.
- `data/registro.json` — id già visti (stato interno, 667 alla migrazione).
- `docs/index.html` + `docs/data/` — la dashboard pubblica (26 avvisi già portati dall'archivio precedente).
- `.github/workflows/check.yml` — il task orario.

## Messa online (una tantum)

1. **Crea il repository** in GitHub Desktop: File → New Repository, nome a
   piacere (es. `interpelli-arianna`), e scegli come cartella locale quella
   dove hai estratto questi file (oppure crea il repo vuoto e poi copia dentro
   tutti i file di questa cartella).
2. **Commit iniziale**: in GitHub Desktop scrivi un messaggio tipo "Primo
   caricamento" e premi *Commit to main*, poi *Publish repository*. Puoi
   lasciarlo **privato** — non serve pubblico per far girare Actions né per la
   dashboard.
3. **App password di Gmail** (serve per mandare le email, non è la password
   normale):
   - vai su https://myaccount.google.com/apppasswords (con l'account Gmail da
     cui vuoi che partano le email)
   - se non hai la verifica in due passaggi attiva, Google te la chiede prima
   - crea una password per l'app, dalle un nome tipo "Interpelli Arianna" e
     copia il codice di 16 caratteri che ti dà
4. **Secrets su GitHub** (Settings del repository → Secrets and variables →
   Actions → New repository secret): aggiungi
   - `GMAIL_ADDRESS` = l'indirizzo Gmail da cui invii
   - `GMAIL_APP_PASSWORD` = il codice di 16 caratteri appena creato
   - `ARIANNA_EMAIL` = `arianna.dauria00@gmail.com`
5. **Attiva GitHub Pages** (Settings → Pages): come *Source* scegli "Deploy
   from a branch", branch `main`, cartella `/docs`, salva. Dopo un minuto la
   dashboard è online su `https://<tuo-utente>.github.io/<nome-repo>/`.
6. *(Opzionale)* **Link nella email**: Settings → Secrets and variables →
   Actions → tab *Variables* → New repository variable `DASHBOARD_URL` con
   l'indirizzo di Pages appena ottenuto — così ogni email avrà anche il link
   alla dashboard.
7. **Primo giro di prova**: tab *Actions* del repository → workflow
   "Controllo interpelli" → *Run workflow* (pulsante a destra) → Run. Dopo
   1-2 minuti controlla che sia verde; se qualcosa non va, apri il log —
   quasi sempre è un secret scritto male.

Da lì in poi il controllo parte da solo ogni ora, 8:00–20:00 italiane, tutti i
giorni, computer acceso o spento.

## Manutenzione
- **Cambiare una fonte**: modifica `fonti_interpelli.json` (regione,
  provincia, url) e fai commit + push da GitHub Desktop.
- **Ora legale**: quando finisce (ultima domenica di ottobre), in
  `.github/workflows/check.yml` cambia `0 6-18 * * *` in `0 7-19 * * *` (c'è
  già scritto nel commento sopra la riga).
- **"Segna come letto"** ora è locale al browser (non c'è più un database
  condiviso come su claude.ai): ogni dispositivo tiene il proprio elenco di
  letti. L'archivio in sé resta unico e condiviso per tutti.
