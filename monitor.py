#!/usr/bin/env python3
"""
Monitoraggio interpelli USR/UAT — profilo Arianna.
Classi di concorso: A013 e A011 (priorita' ALTA), A012/AS12 e A022/AM12 (media).
Solo scuola secondaria di I e II grado.

Uso:
  python3 monitor.py --sources sources.json --seen-dir seen/ --out nuovi.json
Esce con JSON: {controllo, fonti_ok, fonti_totali, errori[], trovati_totali, nuovi[]}
"""
import argparse, concurrent.futures as cf, hashlib, html, json, os, re, ssl, sys, time
import urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta, date

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
CA = "/root/.ccr/ca-bundle.crt"
CTX = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()

# ------------------------------------------------------------ classi di concorso
# Forme accettate: A013 A-013 A.013 A13 A-13 | AS12 AS-12 AM12 AM-12 | A 013 (con UN solo spazio
# e seguito da trattino/parola tipica). NON accetta "a 11" generico (es. "calcio a 11").
CODE_RE = re.compile(r'(?<![A-Za-z0-9])A([SM])?[\s\-–.]?0(11|12|13|22)(?![0-9])', re.I)
CODE_SHORT_RE = re.compile(r'(?<![A-Za-z0-9])A([SM])[\s\-–.]?(11|12|13|22)(?![0-9])', re.I)
CODE_DASH_RE = re.compile(r'(?<![A-Za-z0-9])A[\-–](11|12|13|22)(?![0-9])', re.I)
SOST_RE = re.compile(r'(?<![A-Za-z0-9])AD(SS|MM)(?![A-Za-z0-9])', re.I)

CANON = {'11': 'A011', '12': 'A012', '13': 'A013', '22': 'A022'}
GRADO = {'A013': 'II grado', 'A011': 'II grado', 'A012': 'II grado', 'A022': 'I grado'}
ETICHETTA = {
    'A011': 'A011 — Discipline letterarie e latino (II grado)',
    'A012': 'A012 — Discipline letterarie (II grado)',
    'A013': 'A013 — Discipline letterarie, latino e greco (II grado)',
    'A022': 'A022 — Italiano, storia, geografia (I grado)',
    'ADSS': 'ADSS — Sostegno II grado',
    'ADMM': 'ADMM — Sostegno I grado',
}

# deve parlare di interpello / ricerca supplente
INTERPELLO_RE = re.compile(
    r'interpell|manifestazione di disponibilit|ricerca\s+(?:di\s+)?(?:un\s+)?(?:docente|supplent)|'
    r'avviso.{0,40}reclutamento|individuazione.{0,30}docente|disponibilit.{0,20}supplenz', re.I)

# rumore amministrativo da scartare (se NON contiene "interpello")
RUMORE_RE = re.compile(
    r'graduator|commissione|convocazion|prova orale|prova scritta|titoli e servizi|'
    r'immission[ei] in ruolo|mobilit|utilizzazion|assegnazione provvisoria|'
    r'esito|rettific|errata corrige|decreto di pubblicazione|concorso ordinario|'
    r'calcio|sportiv|campionat|pnrr|corso di formazione|carta docente|'
    r'individuazione.{0,25}(sede|titolar)|sede di titolari|idone[ai].{0,15}30\s*%|'
    r'nomina giuridic|nomine giuridich|prove orali suppletiv|prove suppletive orali|'
    r'estrazione (tracce|letter)|calendario\b|misura compensativ|tirocinio di adattamento|'
    r'\bDD\s*\d{3,4}\s*[/\-]\s*20\d{2}\b|\bDM\s*\d{2,4}\s*[/\-]\s*20\d{2}\b|'
    # voce di menu/categoria per materia (codice + sola descrizione, nessuna scuola):
    r'^A0\d\d?\s*[-–—:.]\s*(discipline letterarie( e latino)?|materie letterarie|'
    r'italiano,?\s*storia,?\s*(e\s*)?geografia)(\s*(negli|nell|nella|per)\b[^0-9]*)?$',
    re.I)

# gradi non pertinenti
ESCLUDI_RE = re.compile(
    r'(?<![A-Za-z0-9])(ADEE|ADAA|AAAA|EEEE|ABEE|AJEE)(?![A-Za-z0-9])|'
    r'scuola primaria|scuola dell.{0,3}infanzia|personale ATA|'
    r'collaborator[ei] scolastic|assistente amministrativ|assistente tecnic|DSGA', re.I)

KEYWORDS = ['discipline letterarie', 'materie letterarie', 'latino e greco',
            'italiano, storia, geografia', 'italiano storia e geografia',
            'italiano, storia e geografia', 'lettere e latino', 'italiano e storia']

# ------------------------------------------------------------------ geografia --
PROV2REG = {
 'AG':'Sicilia','AL':'Piemonte','AN':'Marche','AO':"Valle d'Aosta",'AP':'Marche','AQ':'Abruzzo',
 'AR':'Toscana','AT':'Piemonte','AV':'Campania','BA':'Puglia','BG':'Lombardia','BI':'Piemonte',
 'BL':'Veneto','BN':'Campania','BO':'Emilia-Romagna','BR':'Puglia','BS':'Lombardia','BT':'Puglia',
 'BZ':'Trentino-Alto Adige','CA':'Sardegna','CB':'Molise','CE':'Campania','CH':'Abruzzo',
 'CL':'Sicilia','CN':'Piemonte','CO':'Lombardia','CR':'Lombardia','CS':'Calabria','CT':'Sicilia',
 'CZ':'Calabria','EN':'Sicilia','FC':'Emilia-Romagna','FE':'Emilia-Romagna','FG':'Puglia',
 'FI':'Toscana','FM':'Marche','FR':'Lazio','GE':'Liguria','GO':'Friuli-Venezia Giulia',
 'GR':'Toscana','IM':'Liguria','IS':'Molise','KR':'Calabria','LC':'Lombardia','LE':'Puglia',
 'LI':'Toscana','LO':'Lombardia','LT':'Lazio','LU':'Toscana','MB':'Lombardia','MC':'Marche',
 'ME':'Sicilia','MI':'Lombardia','MN':'Lombardia','MO':'Emilia-Romagna','MS':'Toscana',
 'MT':'Basilicata','NA':'Campania','NO':'Piemonte','NU':'Sardegna','OR':'Sardegna','PA':'Sicilia',
 'PC':'Emilia-Romagna','PD':'Veneto','PE':'Abruzzo','PG':'Umbria','PI':'Toscana','PN':'Friuli-Venezia Giulia',
 'PO':'Toscana','PR':'Emilia-Romagna','PT':'Toscana','PU':'Marche','PV':'Lombardia','PZ':'Basilicata',
 'RA':'Emilia-Romagna','RC':'Calabria','RE':'Emilia-Romagna','RG':'Sicilia','RI':'Lazio',
 'RM':'Lazio','RN':'Emilia-Romagna','RO':'Veneto','SA':'Campania','SI':'Toscana','SO':'Lombardia',
 'SP':'Liguria','SR':'Sicilia','SS':'Sardegna','SU':'Sardegna','SV':'Liguria','TA':'Puglia',
 'TE':'Abruzzo','TN':'Trentino-Alto Adige','TO':'Piemonte','TP':'Sicilia','TR':'Umbria',
 'TS':'Friuli-Venezia Giulia','TV':'Veneto','UD':'Friuli-Venezia Giulia','VA':'Lombardia',
 'VB':'Piemonte','VC':'Piemonte','VE':'Veneto','VI':'Veneto','VR':'Veneto','VT':'Lazio','VV':'Calabria',
}
SIGLA_RE = re.compile(r'\(\s*([A-Z]{2})\s*\)')

MESI = {m: i + 1 for i, m in enumerate(
    ['gennaio','febbraio','marzo','aprile','maggio','giugno','luglio','agosto',
     'settembre','ottobre','novembre','dicembre'])}
DATE_NUM = re.compile(r'\b(\d{1,2})[/\-.](\d{1,2})[/\-.](20\d{2})\b')
DATE_TXT = re.compile(r'\b(\d{1,2})\s*(?:°)?\s+(' + '|'.join(MESI) + r')\s+(20\d{2})\b', re.I)
DATE_ISO = re.compile(r'\b(20\d{2})-(\d{2})-(\d{2})\b')


GENERICO_RE = re.compile(
    r'^(leggi(\s+(di\s+)?(tutto|più|piu|l.articolo))?|continua(\s+a\s+leggere)?|read more|'
    r'scarica|allegat[oi]|download|vai al|clicca qui|qui|apri|dettagli|home|torna|'
    r'avanti|indietro|successiv[oi]|precedent[ei]|\d+)\s*[.…>»]*$', re.I)

PULIZIA_RE = re.compile(
    r'^(timbro|sigillo|annotazione|firmato|signed|protocollo|allegato|all)[_\- ]+|'
    r'[_\- ]*(signed|firmato|timbro|sigillo)$', re.I)


def pulisci_titolo(t):
    t = PULIZIA_RE.sub('', t.strip())
    t = re.sub(r'\.(pdf|docx?|odt|zip)$', '', t, flags=re.I)
    if t.count('_') + t.count('-') > 3 and ' ' not in t.strip()[:40]:
        t = re.sub(r'[_]+', ' ', t)
        t = re.sub(r'(?<=[a-zà-ÿ])-(?=[A-Za-z])', ' ', t)
    return re.sub(r'\s+', ' ', t).strip(' -_.')


def strip_tags(s):
    s = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', s, flags=re.S | re.I)
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', html.unescape(s)).strip()


def trova_data(testo):
    cands = []
    for rx, kind in ((DATE_ISO,'iso'), (DATE_NUM,'num'), (DATE_TXT,'txt')):
        for m in rx.finditer(testo):
            try:
                if kind == 'iso':
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                elif kind == 'num':
                    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                else:
                    d, mo, y = int(m.group(1)), MESI[m.group(2).lower()], int(m.group(3))
                cands.append(date(y, mo, d))
            except Exception:
                pass
    oggi = date.today()
    cands = [c for c in cands if date(2024, 1, 1) <= c <= oggi + timedelta(days=120)]
    if not cands:
        return None
    passate = [c for c in cands if c <= oggi]
    return (max(passate) if passate else min(cands)).isoformat()


def codici(testo):
    out = set()
    for rx in (CODE_RE, CODE_SHORT_RE, CODE_DASH_RE):
        for m in rx.finditer(testo):
            g = m.groups()
            num = g[-1]
            can = CANON[num]
            letter = (g[0] or '').upper() if len(g) > 1 else ''
            if letter == 'M' and can == 'A012':
                can = 'A022'          # AM12 -> primo grado
            if letter == 'S' and can == 'A022':
                can = 'A012'
            out.add(can)
    return sorted(out)


def pertinente(titolo, ctx, vicino=''):
    """-> (codici, motivo) oppure None.
    Un codice di classe di concorso (nel titolo, o nel testo appena
    accanto al link) basta da solo: non deve piu' comparire insieme
    alla parola "interpello"/"manifestazione di disponibilita'" per
    essere segnalato (molti USR/UAT elencano scuola + codice senza
    mai usare quelle parole). La parola "interpello" resta necessaria
    SOLO quando non si trova alcun codice, per accettare titoli tipo
    "discipline letterarie" via KEYWORDS."""
    tl = titolo.lower()
    if ESCLUDI_RE.search(titolo) or ESCLUDI_RE.search(vicino):
        return None
    cods = [c for c in codici(titolo) if c in GRADO]
    if not cods:
        cods = [c for c in codici(vicino) if c in GRADO]
    ha_interpello = bool(INTERPELLO_RE.search(titolo) or
                          (len(titolo) < 45 and INTERPELLO_RE.search(ctx)))
    if not cods and not ha_interpello:
        return None
    if RUMORE_RE.search(titolo) and 'interpell' not in tl:
        return None
    if cods:
        return cods, 'codice'
    if any(k in tl for k in KEYWORDS):
        return ['da verificare'], 'parola chiave'
    return None


SCAD_RE = re.compile(r'entro[^.;\n]{0,60}?(\d{1,2}[/\-.]\d{1,2}[/\-.]20\d{2}|'
                     r'\d{1,2}\s+(?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|'
                     r'settembre|ottobre|novembre|dicembre)\s+20\d{2})', re.I)


def scadenza(testo):
    m = SCAD_RE.search(testo)
    return trova_data(m.group(1)) if m else None


def sede(testo):
    for m in SIGLA_RE.finditer(testo):
        s = m.group(1).upper()
        if s in PROV2REG:
            return s, PROV2REG[s]
    return None, None


STOP = re.compile(r"\b(interpello|interpelli|nazionale|nazionali|regionale|per|supplenza|"
                  r"supplenze|classe|classi|di|del|della|dello|dei|delle|concorso|scuola|scuole|"
                  r"sec|secondaria|secondarie|grado|posto|posti|cattedra|cattedre|ore|"
                  r"docente|docenti|urgente|urgenza|avviso|avvisi|anno|scolastico|art|om|"
                  r"comma|nuovo|nuova|secondo|terzo|breve|temporanea|incarico|disciplina|"
                  r"discipline|letterarie|latino|greco|italiano|storia|geografia|istituti|"
                  r"istituto|istruzione|statale|firmato|signed|con|una|uno|dal|dalla|pdf)\b", re.I)


def ident(link):
    """Identita' di un avviso: il suo indirizzo, normalizzato.
    Non dipende da nessuna espressione regolare, quindi non puo' derivare."""
    p = urllib.parse.urlsplit(link.strip())
    base = (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/").lower())
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def firma(titolo, cods, link):
    t = re.sub(r"[^A-Za-z0-9\u00C0-\u00ff ]", " ", titolo.upper())
    t = re.sub(r"\b20\d{2}\b", " ", t)
    t = STOP.sub(" ", t)
    toks = sorted(set(w for w in t.split() if len(w) > 2))[:12]
    if len(toks) >= 3:
        base = ",".join(cods) + "|" + " ".join(toks)
    else:
        p = urllib.parse.urlsplit(link)
        base = ",".join(cods) + "|url|" + (p.netloc + p.path).lower()
    return hashlib.sha1(base.encode()).hexdigest()[:16]


# ----------------------------------------------------------------- fetching --
def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Connection": "close"})
            with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
                return r.read().decode(r.headers.get_content_charset() or 'utf-8',
                                       errors='replace'), None
        except Exception as e:
            last = str(e)[:90]
            time.sleep(1.5 * (i + 1))
    return None, last


CATEGORIA_RE = re.compile(r'/(category|categoria|categorie|tag)/[^?#]*$', re.I)
URL_TESTO_RE = re.compile(r'^\s*(https?://|www\.)\S+\s*$', re.I)
LINK_RE = re.compile(r'<a\b[^>]*href\s*=\s*["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>', re.S | re.I)
RSS_ITEM_RE = re.compile(r'<item\b.*?</item>', re.S | re.I)


def estrai(url, body):
    out = []
    head = body[:500].lower()
    if '<rss' in head or '<feed' in head:
        for it in RSS_ITEM_RE.findall(body):
            mt = re.search(r'<title>(.*?)</title>', it, re.S | re.I)
            ml = re.search(r'<link>(.*?)</link>', it, re.S | re.I)
            md = re.search(r'<pubDate>(.*?)</pubDate>', it, re.S | re.I)
            data = None
            if md:
                try:
                    from email.utils import parsedate_to_datetime
                    data = parsedate_to_datetime(md.group(1).strip()).date().isoformat()
                except Exception:
                    pass
            testo_item = strip_tags(it)[:500]
            out.append((strip_tags(mt.group(1)) if mt else '',
                        strip_tags(ml.group(1)) if ml else url, data, testo_item, testo_item[:300]))
        return out
    matches = list(LINK_RE.finditer(body))
    for i, m in enumerate(matches):
        href, inner = m.group(1), m.group(2)
        if href.lower().startswith(('javascript:', 'mailto:', 'tel:')):
            continue
        # link a pagine-elenco (categorie, tag): non sono mai un singolo avviso,
        # e il loro "vicino" ruba il codice dell'avviso che segue (caso Messina)
        if CATEGORIA_RE.search(href) or re.search(r'rel\s*=\s*["\'][^"\']*\btag\b', m.group(0), re.I):
            continue
        titolo = strip_tags(inner)
        # testo del link generico ("Leggi di più") o assente: molti siti USR
        # (es. Sicilia) mettono il titolo vero nell'attributo title del link
        if GENERICO_RE.match(titolo) or len(titolo) < 5 or URL_TESTO_RE.match(titolo):
            ta = re.search(r'\btitle\s*=\s*"([^"]{5,400})"|\btitle\s*=\s*\'([^\']{5,400})\'', m.group(0), re.I)
            if ta:
                titolo = strip_tags(ta.group(1) or ta.group(2))
        # titoli corti ("IC Oggiono") ammessi: il filtro sulla lunghezza e' in analizza,
        # dove si sa se accanto c'e' un codice
        if len(titolo) < 5 or len(titolo) > 400:
            continue
        raw = body[max(0, m.start() - 1100): m.end() + 400]
        attr = ' '.join(re.findall(r'datetime\s*=\s*["\']([^"\']{8,30})["\']', raw))
        ctx = strip_tags(raw)
        # "vicino": solo il testo tra la fine di QUESTO link e l'inizio del
        # PROSSIMO link (max 300 caratteri) — mai oltre. Serve a leggere un
        # codice scritto fuori dal link (es. su una riga sotto, come nelle
        # pagine mim.gov.it/.../interpelli-ricerca-supplenti) senza rischiare
        # di rubare il codice della voce successiva in liste fitte (elenchi
        # numerati di PDF, uno via l'altro senza quasi spazio).
        fine_vicino = m.end() + 300
        if i + 1 < len(matches):
            fine_vicino = min(fine_vicino, matches[i + 1].start())
        vicino = strip_tags(re.sub(r'<[^>]*$', '', body[m.end(): max(m.end(), fine_vicino)]))
        # tabelle dove il testo del link e' l'indirizzo stesso (es. Savona):
        # il titolo utile e' il resto della riga della tabella
        if URL_TESTO_RE.match(titolo):
            a_ = body.rfind('<tr', 0, m.start()); b_ = body.find('</tr>', m.end())
            if a_ >= 0 and b_ > 0 and b_ - a_ < 6000 and body.rfind('</tr>', a_, m.start()) < 0:
                riga = strip_tags(body[a_:b_])
                riga = re.sub(r'https?://\S+', ' ', riga)
                titolo = re.sub(r'\s+', ' ', riga).strip()[:220]
                vicino = titolo
            elif len(vicino) >= 12:
                titolo = vicino[:200]
        out.append((titolo, urllib.parse.urljoin(url, html.unescape(href.strip())),
                    trova_data(attr) or trova_data(ctx), ctx[:500], vicino[:300]))
    return out


# ----------------------------------------------------------------- PDF --
# Id gia' visti (registro): impostato da main() prima di analizzare le fonti.
# Serve a non riscaricare ogni ora gli stessi PDF.
SEEN = set()
PDF_CONTROLLATI = set()          # PDF letti in questo giro (vanno nel registro)
MAX_PDF_PER_FONTE = 12
PDF_RE = re.compile(r'\.pdf(\?|/|$)', re.I)


def nome_file(link):
    """'.../timbro_INTERPELLO-A013-signed.pdf' -> 'timbro INTERPELLO A013 signed'"""
    p = urllib.parse.unquote(urllib.parse.urlsplit(link).path.rstrip('/'))
    segs = p.split('/')
    base = next((x for x in reversed(segs) if re.search(r'\.(pdf|p7m|docx?|odt)$', x, re.I)), segs[-1])
    base = re.sub(r'\.(pdf|p7m|docx?|odt|zip)$', '', base, flags=re.I)
    return re.sub(r'[_\-.+]+', ' ', base).strip()


def scarica(url, limite=12_000_000):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Connection": "close",
                                                   "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
                                                   "Accept": "application/pdf,*/*;q=0.8"})
        with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
            return r.read(limite)
    except Exception:
        return None


def testo_pdf(url, pagine=3):
    """Testo delle prime pagine di un PDF ('' se non leggibile). Usa pdftotext,
    altrimenti pypdf. Un PDF scansionato (solo immagine) resta illeggibile."""
    dati = scarica(url)
    if not dati or not dati.lstrip()[:5].startswith(b'%PDF'):
        return ''
    import subprocess, tempfile
    try:
        with tempfile.NamedTemporaryFile(suffix='.pdf') as f:
            f.write(dati); f.flush()
            r = subprocess.run(['pdftotext', '-l', str(pagine), '-layout', f.name, '-'],
                               capture_output=True, text=True, timeout=40)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
    except Exception:
        pass
    try:
        import io
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(dati))
        return '\n'.join((p.extract_text() or '') for p in rd.pages[:pagine])
    except Exception:
        return ''


OGGETTO_RE = re.compile(r'oggetto\s*:\s*(.{15,400}?)(?:\n\s*\n|\bVIST[AOE]\b|\bIL DIRIGENTE\b|$)', re.I | re.S)


def oggetto_pdf(testo):
    m = OGGETTO_RE.search(testo)
    return re.sub(r'\s+', ' ', m.group(1)).strip()[:240] if m else ''


# ------------------------------------------------ tabelle USR Piemonte --
# servizi.istruzionepiemonte.it/interpello2025/ric_interpello_ambito_XX.php:
# nessun link, una riga di tabella per interpello (il PDF si genera con un
# pulsante "Stampa"). Tutto cio' che serve e' gia' nella riga.
TR_RE = re.compile(r'<tr\b.*?</tr>', re.S | re.I)
TD_RE = re.compile(r'<td\b[^>]*>(.*?)</td>', re.S | re.I)
MECC_RE = re.compile(r'\b([A-Z]{2})[A-Z]{2}\d{5}[0-9A-Z]\b')


def _data_piemonte(s):
    s = s.strip()
    m = re.match(r'(\d{1,2})/(\d{1,2})/?(20\d{2})$', s)       # anche "21/092026"
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except Exception:
            return None
    m = re.match(r'(20\d{2})-(\d{2})-(\d{2})', s)
    return m.group(0) if m else None


def tabella_piemonte(url, body):
    """-> lista di item gia' pronti (solo interpelli 'aperto' delle classi monitorate)."""
    out = []
    for tr in TR_RE.findall(body):
        celle = [strip_tags(c) for c in TD_RE.findall(tr)]
        if len(celle) < 10 or not MECC_RE.match(celle[0] or ''):
            continue
        mecc, scuola, cdc_txt, tipo, corso, durata, d_int = celle[:7]
        stato, d_scad = celle[8].lower(), celle[9]
        if 'aperto' not in stato:
            continue
        if ESCLUDI_RE.search(cdc_txt):
            continue
        cods = [c for c in codici(cdc_txt) if c in GRADO]
        if not cods:
            continue
        pm = re.search(r'name\s*=\s*"progr"\s+value\s*=\s*"(\d+)"', tr)
        chiave = pm.group(1) if pm else f"{mecc}-{cdc_txt[:4]}-{d_int}"
        sigla = MECC_RE.match(mecc).group(1)
        titolo = f"{scuola} — {cdc_txt}"
        dett = ' · '.join(x for x in (tipo, durata, corso if corso.lower() != 'diurno' else '') if x and x != '-')
        out.append({
            'id': ident(url.rstrip('/') + '/progr/' + chiave),
            'titolo': titolo[:280] + (f" ({dett})" if dett else ''),
            'link': url,
            'cods': cods, 'data': _data_piemonte(d_int), 'scadenza': _data_piemonte(d_scad),
            'sede_prov': sigla if sigla in PROV2REG else None,
            'sede_regione': PROV2REG.get(sigla), 'motivo': 'tabella',
            'estratto': ' | '.join(celle[:10])[:280],
        })
    return out


def _item(titolo, link, cods, motivo, data, ctx, reg, prov, url, id_=None, scad=None, sg=None, sreg=None):
    prio = 'alta' if any(c in ('A013', 'A011') for c in cods) else (
        'media' if any(c in ('A012', 'A022') for c in cods) else 'bassa')
    if sg is None:
        sg, sreg = sede(titolo)
    return {
        'id': id_ or ident(link),
        'firma': firma(titolo, cods, link),
        'titolo': re.sub(r'\s+', ' ', titolo)[:280],
        'link': link,
        'cdc': cods,
        'cdc_desc': [ETICHETTA.get(c, c) for c in cods],
        'priorita': prio,
        'grado': sorted({GRADO[c] for c in cods if c in GRADO}) or ['da verificare'],
        'data': data,
        'scadenza': scad or scadenza(titolo) or scadenza(ctx),
        'sede_prov': sg,
        'sede_regione': sreg,
        'ufficio_regione': reg,
        'ufficio': prov,
        'fonte': url,
        'motivo': motivo,
        'estratto': ctx[:280],
    }


def analizza(src):
    reg, prov, url = src
    body, err = fetch(url)
    if body is None:
        return {'regione': reg, 'provincia': prov, 'url': url, 'errore': err, 'items': []}
    items, visti = [], set()

    if 'stato interpello' in body.lower() and 'ric_interpello' in url:
        for t in tabella_piemonte(url, body):
            items.append(_item(t['titolo'], t['link'], t['cods'], t['motivo'], t['data'],
                               t['estratto'], reg, prov, url, id_=t['id'], scad=t['scadenza'],
                               sg=t['sede_prov'], sreg=t['sede_regione']))
        return {'regione': reg, 'provincia': prov, 'url': url, 'errore': None, 'items': items}

    pdf_letti = 0
    for titolo, link, data, ctx, vicino in estrai(url, body):
        if GENERICO_RE.match(titolo.strip()):
            continue
        titolo = pulisci_titolo(titolo)
        # titoli corti ("IC Oggiono") ammessi solo se accanto c'e' un codice
        if len(titolo) < 12 and not codici(vicino):
            continue
        if len(titolo) < 5:
            continue
        res = pertinente(titolo, ctx, vicino)
        motivo_extra = None
        if not res:
            # 1) il codice sta solo nel nome del file (".../INTERPELLO-A013-signed.pdf")
            nf = nome_file(link)
            if nf and not ESCLUDI_RE.search(nf) and INTERPELLO_RE.search(titolo + ' ' + nf):
                cf_ = [c for c in codici(nf) if c in GRADO]
                if cf_ and not (RUMORE_RE.search(titolo) and 'interpell' not in titolo.lower()):
                    res, motivo_extra = (cf_, 'codice'), 'nome file'
            # 2) PDF con titolo generico ("Interpello prot. 7385 del 23/09/2026"):
            #    si apre il PDF e si cerca il codice nel testo
            if (not res and PDF_RE.search(link) and pdf_letti < MAX_PDF_PER_FONTE
                    and INTERPELLO_RE.search(titolo + ' ' + nf)
                    and not ESCLUDI_RE.search(titolo + ' ' + nf)
                    and not (RUMORE_RE.search(titolo) and 'interpell' not in titolo.lower())
                    and (not data or data >= ARCHIVIO_DAL)):
                pid = ident(link)
                if pid not in SEEN and pid not in PDF_CONTROLLATI:
                    pdf_letti += 1
                    PDF_CONTROLLATI.add(pid)
                    tp = testo_pdf(link)
                    cp = [c for c in codici(tp[:6000]) if c in GRADO]
                    if cp:
                        ogg = oggetto_pdf(tp)
                        if ogg and len(titolo) < 60:
                            titolo = f"{titolo} — {ogg}"
                        res, motivo_extra = (cp, 'codice'), 'letto nel PDF'
        if not res:
            continue
        cods, motivo = res
        if motivo_extra:
            motivo = motivo_extra
        key = (link, titolo[:90])
        if key in visti:
            continue
        visti.add(key)
        items.append(_item(titolo, link, cods, motivo, data, ctx, reg, prov, url))
    return {'regione': reg, 'provincia': prov, 'url': url, 'errore': None, 'items': items}


# ------------------------------------------------------------ dettagli del singolo avviso
# L'archivio parte da questa data (richiesta di Gabriele, 21/09/2026): nulla di
# pubblicato prima entra nella dashboard.
ARCHIVIO_DAL = '2026-09-21'
ROMA = None
try:
    from zoneinfo import ZoneInfo
    ROMA = ZoneInfo('Europe/Rome')
except Exception:
    pass

FILE_RE = re.compile(r'\.(pdf|zip|rar|7z|docx?|odt|p7m|xlsx?|ods|rtf)(\?|$)', re.I)
GIORNO_RE = re.compile(r'\b(?:luned[iì]|marted[iì]|mercoled[iì]|gioved[iì]|venerd[iì]|sabato|domenica),?\s+'
                       r'(\d{1,2})\s+(' + '|'.join(MESI) + r')\s+(20\d{2})\b', re.I)
META_TEMPO_RE = re.compile(
    r'(?:article:published_time|datePublished|dc\.date\.issued|dcterms\.created)["\']?\s*'
    r'(?:content=|:)\s*["\'](20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}[^"\']*)["\']', re.I)
META_DATA_RE = re.compile(r'<meta[^>]+(?:name|property)\s*=\s*["\'](?:DC\.date[\w.]*|date|dcterms\.\w+|'
                          r'article:published_time)["\'][^>]*content\s*=\s*["\'](20\d{2}-\d{2}-\d{2})', re.I)
META_DATA2_RE = re.compile(r'<meta[^>]+content\s*=\s*["\'](20\d{2}-\d{2}-\d{2})[^"\']*["\'][^>]*(?:name|property)\s*=\s*'
                           r'["\'](?:DC\.date[\w.]*|date|dcterms\.\w+|article:published_time)["\']', re.I)
SCAD_DETTAGLIO_RE = re.compile(r'scadenz\w*[^0-9]{0,25}(\d{1,2}[/\-.]\d{1,2}[/\-.]20\d{2}|'
                               r'\d{1,2}\s+(?:' + '|'.join(MESI) + r')\s+20\d{2})', re.I)


def _ora_roma(dt):
    if ROMA:
        dt = dt.astimezone(ROMA)
    return dt.isoformat(timespec='minutes')


def _richiesta(url, metodo):
    req = urllib.request.Request(url, method=metodo, headers={
        "User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9", "Connection": "close",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
    return urllib.request.urlopen(req, timeout=25, context=CTX)


# ------------------------------------------------ stesso interpello su piu' siti
# Un interpello nazionale viene ripubblicato da molti uffici, ognuno con titolo e
# indirizzo suoi. Lo si riconosce dal DOCUMENTO: tutte le copie contengono la
# stessa lettera della scuola (codice meccanografico, protocollo della scuola,
# primo timbro dell'ufficio che l'ha ricevuta, oggetto).
AOO_RE = re.compile(r'AOO\s*([A-Z]{3,8})[\W_]{0,40}REGISTRO[\W_]{0,40}UFFICIALE[\W_]{0,6}(?:[EU][\W_]{0,4})?0*(\d{2,7})[\W_]{1,4}'
                    r'(\d{2})[\W_](\d{2})[\W_](20\d{2})', re.I)
EMAIL_MECC_RE = re.compile(r'\b([a-z]{4}\d{5}[0-9a-z])@(?:pec\.)?istruzione\.it', re.I)
PROT_SCUOLA_RE = re.compile(r'REGISTRO\s+UFFICIALE\s*\(\s*(?:U|Uscita)\s*\)\s*-\s*0*(\d{2,7})', re.I)


def _h(s):
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def impronte(testo, cods, nomi_file=()):
    """Chiavi che identificano il documento dell'interpello, qualunque sia il sito."""
    cc = ','.join(sorted(cods))
    fp = set()
    for nf in nomi_file:
        m = AOO_RE.search(nf.replace(' ', '.'))
        if m:
            fp.add('a' + _h(f"{m.group(1).upper()}|{int(m.group(2))}|{m.group(5)}{m.group(4)}{m.group(3)}|{cc}"))
    if testo:
        m = AOO_RE.search(testo)
        if m:
            fp.add('a' + _h(f"{m.group(1).upper()}|{int(m.group(2))}|{m.group(5)}{m.group(4)}{m.group(3)}|{cc}"))
        me = EMAIL_MECC_RE.search(testo)
        if me:
            mecc = me.group(1).upper()
            mp = PROT_SCUOLA_RE.search(testo)
            if mp:
                fp.add('p' + _h(f"{mecc}|{int(mp.group(1))}|{cc}"))
            else:
                # senza protocollo: oggetto + prima data del documento, cosi' due
                # interpelli diversi della stessa scuola con lo stesso oggetto
                # generico (settimane dopo) NON vengono fusi
                ogg = re.sub(r'[^a-z0-9]', '', oggetto_pdf(testo).lower())[:160]
                dm = DATE_NUM.search(testo) or DATE_TXT.search(testo)
                if len(ogg) >= 25 and dm:
                    fp.add('o' + _h(f"{mecc}|{ogg}|{dm.group(0)}|{cc}"))
    return sorted(fp)


def _impronte_da_pdf(it, pdf_links):
    """Legge al massimo due PDF allegati e ne ricava le impronte. Un PDF conta
    solo se nomina una classe di concorso dell'avviso: un allegato estraneo
    (privacy, modulo) non deve mai far fondere due avvisi diversi."""
    fp = set(impronte('', it['cdc'], [nome_file(l) for l in pdf_links]))
    for l in pdf_links[:2]:
        tp = testo_pdf(l, pagine=2)
        if tp and set(codici(tp[:8000])) & set(it['cdc']):
            fp.update(impronte(tp, it['cdc']))
            if not it.get('scadenza'):
                s = SCAD_DETTAGLIO_RE.search(tp) or SCAD_RE.search(tp)
                if s:
                    it['scadenza'] = trova_data(s.group(1))
    return sorted(fp)


def dettagli(it):
    """Apre il link dell'avviso e aggiunge, se li trova: 'pubblicato' (data e ora,
    ora di Roma), 'data' se mancava, 'scadenza' se mancava, e 'impronte' (per
    riconoscere lo stesso interpello pubblicato su piu' siti). Non alza mai eccezioni."""
    link = it['link']
    try:
        it['impronte'] = []
        if it.get('motivo') == 'tabella':
            return it
        if PDF_RE.search(link):
            try:
                it['impronte'] = _impronte_da_pdf(it, [link])
            except Exception:
                pass
        if FILE_RE.search(link):
            # file allegato: la data/ora di caricamento sta nell'intestazione Last-Modified
            try:
                r = _richiesta(link, 'HEAD')
            except Exception:
                r = _richiesta(link, 'GET')
            with r:
                lm = r.headers.get('Last-Modified')
            if lm:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(lm)
                it['pubblicato'] = _ora_roma(dt)
                it['data'] = it['data'] or it['pubblicato'][:10]
            return it
        body, _ = fetch(link, tries=2)
        if not body:
            return it
        m = META_TEMPO_RE.search(body)
        if m:
            try:
                dt = datetime.fromisoformat(m.group(1).replace('Z', '+00:00'))
                if dt.tzinfo is None and ROMA:
                    dt = dt.replace(tzinfo=ROMA)
                it['pubblicato'] = _ora_roma(dt)
            except Exception:
                pass
        testo = strip_tags(body)
        if not it.get('data'):
            md = META_DATA_RE.search(body) or META_DATA2_RE.search(body)
            if md:
                it['data'] = md.group(1)
        g = GIORNO_RE.search(testo)
        if g:
            try:
                d = date(int(g.group(3)), MESI[g.group(2).lower()], int(g.group(1))).isoformat()
                it['data'] = d          # la data della pagina dell'avviso vince su quella dell'elenco
            except Exception:
                pass
        elif it.get('pubblicato'):
            it['data'] = it['pubblicato'][:10]
        if not it.get('scadenza'):
            s = SCAD_DETTAGLIO_RE.search(testo) or SCAD_RE.search(testo)
            if s:
                it['scadenza'] = trova_data(s.group(1)) or it.get('scadenza')
        allegati = []
        for h, _ in LINK_RE.findall(body):
            u = urllib.parse.urljoin(link, html.unescape(h.strip()))
            if PDF_RE.search(u) and u not in allegati:
                allegati.append(u)
        if allegati:
            it['impronte'] = _impronte_da_pdf(it, allegati)
    except Exception:
        pass
    return it


VETTORE = ("Interpello nazionale per supplenza classe di concorso: A013 - Discipline "
           "letterarie presso l'Istituto Leonardo da Vinci di Lanusei",
           ["A013"], "https://www.mim.gov.it/web/nuoro/-/interpello-a013-lanusei")

# Caso reale segnalato da Arianna (Lombardia, 21/09/2026): pagine che elencano
# scuola + codice SENZA mai scrivere "interpello" o "manifestazione di
# disponibilita'". Deve essere accettato lo stesso, dal solo codice.
VETTORE_CODICE_NUDO = ("IC Bosisio Parini", "", "IC Bosisio Parini Classe di concorso AM12")


def autodiagnosi():
    ok = True
    if not STOP.search("interpello nazionale per supplenza"):
        print("ERRORE: la lista di parole comuni non funziona (escaping alterato)", file=sys.stderr); ok = False
    if ident(VETTORE[2]) != "426518f7142177e2":
        print("ERRORE: identita' degli avvisi alterata", file=sys.stderr); ok = False
    if firma(*VETTORE) != "a3e336f7f38c6c4f":
        print("ERRORE: firma dei titoli alterata", file=sys.stderr); ok = False
    res = pertinente(*VETTORE_CODICE_NUDO)
    if res != (['A022'], 'codice'):
        print("ERRORE: un avviso col solo codice (senza la parola 'interpello') "
              "non viene piu' riconosciuto — regressione del bug segnalato da Arianna",
              file=sys.stderr)
        ok = False
    if pertinente("Convocazione commissione esame A013", "", "Convocazione commissione esame A013") is not None:
        print("ERRORE: il filtro rumore (convocazioni/commissioni) non funziona piu'", file=sys.stderr)
        ok = False
    # Casi segnalati da Arianna il 23/09/2026
    html_lecco = ('<h3><a href="https://www.mim.gov.it/web/lecco/-/ic-oggiono-24">IC Oggiono</a></h3>'
                  '<p>Classe di concorso AM12</p><h3><a href="https://x.it/-/altro">IC Altro Istituto</a></h3>')
    righe = [r for r in estrai('https://www.mim.gov.it/web/lecco/x', html_lecco) if 'oggiono' in r[1]]
    if not righe or len(righe[0][0]) >= 12 or not codici(righe[0][4]):
        print("ERRORE: i titoli corti con il codice accanto (es. 'IC Oggiono') si perdono di nuovo", file=sys.stderr)
        ok = False
    html_sic = ('<strong><a href="https://tp.usr.sicilia.it/category/interpelli/" rel="category tag">Interpelli</a></strong>'
                '<h4>Interpello CdC AM12 al 13/10</h4><a href="https://tp.usr.sicilia.it/interpello-am12/" '
                'title="Interpello per incarico docente CdC AM12 al 13/10/2026" class="read-more">Leggi di pi&ugrave;</a>')
    tit = [r[0] for r in estrai('https://tp.usr.sicilia.it/category/interpelli/', html_sic)]
    if tit != ['Interpello per incarico docente CdC AM12 al 13/10/2026']:
        print("ERRORE: titolo dall'attributo title / scarto dei link di categoria non funziona", tit, file=sys.stderr)
        ok = False
    riga = ('<tr><td>ALIS016008</td><td>ISTITUTO SUPERIORE UMBERTO ECO</td><td>A013 - Discipline letterarie, '
            'latino e greco</td><td>Spezzone</td><td>Diurno</td><td>Fino al 30 giugno</td><td>22/09/2026</td>'
            '<td><input type="hidden" name="progr" value="1802"></td><td>aperto</td><td>2026-09-24</td>'
            '<td>-</td><td>-</td></tr>')
    tp_ = tabella_piemonte('https://servizi.istruzionepiemonte.it/interpello2025/ric_interpello_ambito_al.php', riga)
    if len(tp_) != 1 or tp_[0]['cods'] != ['A013'] or tp_[0]['scadenza'] != '2026-09-24':
        print("ERRORE: le tabelle interpelli del Piemonte non vengono piu' lette", file=sys.stderr)
        ok = False
    lettera = ("REGISTRO UFFICIALE (Uscita) - 0012304 - VII.1\nm_pi.AOOUSPIS.REGISTRO\n   UFFICIALE.E.0003706.22-09-2026\n"
               "email: isic82600e@istruzione.it\nOggetto: Interpello classe di concorso AM12 per n. 18 ore al 13/10/2026.\n\nVISTA")
    a1 = impronte(lettera, ['A022'])
    a2 = impronte("copia inoltrata\n" + lettera, ['A022'], ['m pi AOOUSPSR REGISTRO UFFICIALE E 0021077 22 09 2026'])
    if not a1 or not set(a1) & set(a2):
        print("ERRORE: lo stesso interpello su due siti non viene piu' riconosciuto", file=sys.stderr)
        ok = False
    print("autodiagnosi:", "ok" if ok else "FALLITA")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sources', default='sources.json')
    ap.add_argument('--seen-file', default=None, help='JSON con gli id gia visti')
    ap.add_argument('--out', default='nuovi.json')
    ap.add_argument('--workers', type=int, default=16)
    ap.add_argument('--giorni', type=int, default=45, help='scarta avvisi piu vecchi di N giorni')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()

    if a.selftest:
        sys.exit(0 if autodiagnosi() else 1)

    srcs = json.load(open(a.sources))
    seen = set()
    if a.seen_file and os.path.exists(a.seen_file):
        try:
            raw = json.load(open(a.seen_file))
            seen = set(raw.get('ids', raw) if isinstance(raw, dict) else raw)
        except Exception as e:
            print('ATTENZIONE: registro illeggibile:', e, file=sys.stderr)
    SEEN.update(seen)

    with cf.ThreadPoolExecutor(a.workers) as ex:
        report = list(ex.map(analizza, srcs))

    limite = (date.today() - timedelta(days=a.giorni)).isoformat()
    unione, tutti_id, per_firma, ripub_firma = {}, set(), {}, {}
    for r in report:
        for it in r['items']:
            tutti_id.add(it['id'])
            if it['data'] and it['data'] < limite:
                continue
            chiave = per_firma.get(it['firma'], it['id'])
            cur = unione.get(chiave)
            if cur is None:
                it['anche_su'] = []
                unione[it['id']] = it
                per_firma[it['firma']] = it['id']
            else:
                et = f"{it['ufficio_regione']}/{it['ufficio']}"
                if et not in cur['anche_su'] and et != f"{cur['ufficio_regione']}/{cur['ufficio']}":
                    cur['anche_su'].append(et)
                    if cur['id'] in seen and it['id'] not in seen:
                        # copia nuova di un avviso gia' in archivio (stesso titolo)
                        r_ = ripub_firma.setdefault(cur['id'], {'doc_id': cur['id'], 'uffici': [], 'link': []})
                        r_['uffici'].append(et); r_['link'].append(it['link'])
                if not cur['data'] and it['data']:
                    cur['data'] = it['data']
                if not cur['sede_prov'] and it['sede_prov']:
                    cur['sede_prov'], cur['sede_regione'] = it['sede_prov'], it['sede_regione']

    tutti = list(unione.values())
    nuovi = [it for it in tutti if it['id'] not in seen]
    # apre ogni avviso nuovo per data/ora di pubblicazione e scadenza precise
    with cf.ThreadPoolExecutor(8) as ex:
        nuovi = list(ex.map(dettagli, nuovi))
    # l'archivio parte da ARCHIVIO_DAL: via cio' che risulta pubblicato prima
    # (resta nel registro tramite tutti_gli_id, quindi non ricompare piu')
    scartati_vecchi = [it for it in nuovi if it['data'] and it['data'] < max(ARCHIVIO_DAL, limite)]
    nuovi = [it for it in nuovi if not (it['data'] and it['data'] < max(ARCHIVIO_DAL, limite))]

    # stesso interpello ripubblicato da altri uffici (in questo giro o in giri
    # precedenti): una sola scheda, gli altri uffici finiscono in "anche_su".
    # Nel registro le impronte stanno come "fp:<impronta>:<id della scheda>".
    fpmap = {}
    for s_ in seen:
        if s_.startswith('fp:'):
            parti = s_.split(':', 2)
            if len(parti) == 3:
                fpmap[parti[1]] = parti[2]
    tenuti, per_id, ripub = [], {}, dict(ripub_firma)
    for it in nuovi:
        fps = it.pop('impronte', None) or []
        dove = f"{it['ufficio_regione']}/{it['ufficio']}"
        gia = next((fpmap[k] for k in fps if k in fpmap), None)
        if gia:
            for k in fps:
                fpmap.setdefault(k, gia)
            if gia in per_id:                       # scheda nata in questo stesso giro
                cur = per_id[gia]
                if dove not in cur['anche_su'] and dove != f"{cur['ufficio_regione']}/{cur['ufficio']}":
                    cur['anche_su'].append(dove)
            else:                                   # scheda gia' in archivio
                r_ = ripub.setdefault(gia, {'doc_id': gia, 'uffici': [], 'link': []})
                if dove not in r_['uffici']:
                    r_['uffici'].append(dove); r_['link'].append(it['link'])
            continue
        for k in fps:
            fpmap[k] = it['id']
        tenuti.append(it); per_id[it['id']] = it
    nuovi = tenuti
    impronte_reg = {f"fp:{k}:{d}" for k, d in fpmap.items()} - seen
    tutti_id |= PDF_CONTROLLATI | impronte_reg
    ordine = {'alta': 0, 'media': 1, 'bassa': 2}
    nuovi.sort(key=lambda x: (ordine[x['priorita']], '9999' if not x['data'] else x['data']))
    nuovi.reverse()
    nuovi.sort(key=lambda x: ordine[x['priorita']])

    errori = [{'regione': r['regione'], 'provincia': r['provincia'], 'url': r['url'],
               'errore': r['errore']} for r in report if r['errore']]
    now = datetime.now(timezone(timedelta(hours=2))).isoformat(timespec='seconds')
    out = {'controllo': now, 'fonti_totali': len(srcs), 'fonti_ok': len(srcs) - len(errori),
           'errori': errori, 'trovati_totali': len(tutti),
           'tutti_gli_id': sorted(tutti_id), 'nuovi': nuovi,
           'ripubblicazioni': list(ripub.values())}
    json.dump(out, open(a.out, 'w'), ensure_ascii=False, indent=1)
    print(f"fonti ok {out['fonti_ok']}/{out['fonti_totali']} | unici {len(tutti)} | NUOVI {len(nuovi)}"
          f" | scartati perche' pubblicati prima del {ARCHIVIO_DAL}: {len(scartati_vecchi)}")
    for it in nuovi[:60]:
        loc = it['sede_prov'] or it['ufficio']
        print(f"  [{it['priorita']:5}] {','.join(it['cdc']):10} {str(it['data']):10} {loc:4} {it['titolo'][:88]}")
    for r_ in ripub.values():
        print(f"  gia' in archivio ({r_['doc_id']}), ripubblicato anche da: {', '.join(r_['uffici'])}")
    if PDF_CONTROLLATI:
        print(f"  PDF aperti e letti in questo giro: {len(PDF_CONTROLLATI)}")
    if errori:
        print("NON RAGGIUNGIBILI:", ', '.join(f"{e['regione']}/{e['provincia']}" for e in errori))


if __name__ == '__main__':
    main()
