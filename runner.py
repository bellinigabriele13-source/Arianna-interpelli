#!/usr/bin/env python3
"""Orchestratore per il monitoraggio interpelli, pensato per girare dentro
GitHub Actions una volta all'ora. Chiama monitor.py (lo scraper), aggiorna
l'archivio e il registro locali (committati nel repository), e manda
un'email ad Arianna solo quando ci sono avvisi davvero nuovi.

Questo sistema e' indipendente da Claude: non tocca ne' l'artefatto ne' il
task orario che giravano prima su claude.ai."""
import json
import os
import smtplib
import ssl
import subprocess
import sys
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

BASE = Path(__file__).resolve().parent
REGISTRO = BASE / 'data' / 'registro.json'
ARCHIVIO = BASE / 'docs' / 'data' / 'interpelli.json'
CONTROLLO = BASE / 'docs' / 'data' / 'controllo.json'
SEEN_TMP = BASE / '_seen.json'
NUOVI_TMP = BASE / '_nuovi.json'
MAX_ARCHIVIO = 4500  # pulizia automatica solo oltre questa soglia (limite tecnico 5000)


def ora_roma():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo('Europe/Rome')).isoformat(timespec='seconds')


def carica(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception as e:
            print(f'ATTENZIONE: {path} illeggibile: {e}', file=sys.stderr)
    return default


def salva(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding='utf-8')


def invia_email(nuovi):
    mitt = os.environ.get('GMAIL_ADDRESS')
    pwd = os.environ.get('GMAIL_APP_PASSWORD')
    dest = os.environ.get('ARIANNA_EMAIL', 'arianna.dauria00@gmail.com')
    url_dashboard = os.environ.get('DASHBOARD_URL', '').strip()

    if not (mitt and pwd):
        print('ATTENZIONE: GMAIL_ADDRESS / GMAIL_APP_PASSWORD mancanti, email NON inviata', file=sys.stderr)
        return

    blocchi = []
    for it in nuovi:
        pubblicato = it.get('pubblicato') or (
            f"{it.get('data')} (ora non indicata dal sito)" if it.get('data') else 'data non nota'
        )
        riga_anche_su = f"\n  Anche su: {', '.join(it['anche_su'])}" if it.get('anche_su') else ''
        blocchi.append(
            f"[{','.join(it.get('cdc') or [])}] {it['titolo']}\n"
            f"  Sede: {it.get('sede_prov') or it.get('ufficio') or '—'}"
            f"  |  Ufficio: {it.get('ufficio', '—')} ({it.get('ufficio_regione', '—')})\n"
            f"  Pubblicato: {pubblicato}  |  Trovato: {it.get('aggiunto', '—')}"
            f"{riga_anche_su}\n"
            f"  {it['link']}"
        )

    corpo = f"Nuovi interpelli trovati ({len(nuovi)}):\n\n" + "\n\n".join(blocchi) + "\n"
    if url_dashboard:
        corpo += f"\nDashboard: {url_dashboard}\n"

    codici = sorted({c for it in nuovi for c in (it.get('cdc') or [])})
    msg = MIMEText(corpo, 'plain', 'utf-8')
    msg['Subject'] = f"{len(nuovi)} nuovo/i interpello/i" + (f" ({', '.join(codici)})" if codici else '')
    msg['From'] = mitt
    msg['To'] = dest

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx) as s:
        s.login(mitt, pwd)
        s.sendmail(mitt, [dest], msg.as_string())
    print('email inviata a', dest)


def main():
    registro = carica(REGISTRO, {'ids': []})
    seen = set(registro.get('ids', []))
    salva(SEEN_TMP, {'ids': sorted(seen)})

    subprocess.run(
        [
            sys.executable, str(BASE / 'monitor.py'),
            '--sources', str(BASE / 'fonti_interpelli.json'),
            '--seen-file', str(SEEN_TMP),
            '--out', str(NUOVI_TMP),
        ],
        check=True,
    )

    out = json.loads(NUOVI_TMP.read_text(encoding='utf-8'))
    nuovi = out['nuovi']
    ripub = out['ripubblicazioni']
    ora = ora_roma()

    archivio = carica(ARCHIVIO, [])
    per_id = {a['id']: a for a in archivio}

    for it in nuovi:
        it['letto'] = False
        it['aggiunto'] = ora
        per_id[it['id']] = it

    for r in ripub:
        doc = per_id.get(r['doc_id'])
        if not doc:
            continue
        doc.setdefault('anche_su', [])
        for u in r['uffici']:
            if u not in doc['anche_su']:
                doc['anche_su'].append(u)

    archivio = sorted(per_id.values(), key=lambda a: a.get('aggiunto') or a.get('data') or '')
    if len(archivio) > MAX_ARCHIVIO:
        archivio = archivio[-MAX_ARCHIVIO:]

    salva(ARCHIVIO, archivio)
    salva(REGISTRO, {'ids': out['tutti_gli_id']})
    salva(CONTROLLO, {
        'ultimo': ora,
        'fonti_ok': out['fonti_ok'],
        'fonti_totali': out['fonti_totali'],
        'errori': out['errori'],
        'nuovi_ultimo_giro': len(nuovi),
    })

    print(f"nuovi: {len(nuovi)} | ripubblicazioni: {len(ripub)} | archivio: {len(archivio)}")

    if nuovi:
        invia_email(nuovi)

    SEEN_TMP.unlink(missing_ok=True)
    NUOVI_TMP.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
