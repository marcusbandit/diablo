#!/usr/bin/env python3
import fcntl
import html
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

STATE = Path.home() / '.local/state/hermes/mail-code-monitor.json'
LOCK = Path.home() / '.local/state/hermes/mail-code-monitor.lock'
HIMALAYA = '/usr/local/bin/himalaya'
HERMES_ENV = Path.home() / '.hermes/.env'
DIABLO_HUB_DB = Path(os.environ.get('DIABLO_HUB_DB') or os.path.expanduser('~/diablo-hub/diablo.db'))
_saved_text = None
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID') or '5959415319'
ATTACH_DIR = Path.home() / '.local/share/hermes/mail-attachments'

HINT_RE = re.compile(
    r'(verification|verify|code|pin|sign[ -]?in|login|log in|one[- ]?time|otp|2fa|two[- ]?factor|security code|authenticate|confirm|bekræft|password|booking|reservation|message from|special request)',
    re.I,
)

CODE_PATTERNS = [
    re.compile(r'(?<!\d)(\d{4,8})(?!\d)'),
    re.compile(r'(?<!\d)(\d{3}[ -]\d{3}|\d{4}[ -]\d{4}|\d{2}[ -]\d{2}[ -]\d{2})(?!\d)'),
    re.compile(r'\b(?=[A-Z0-9]{5,10}\b)(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{5,10}\b'),
    re.compile(r'(?<![A-Z0-9])([A-Z0-9]{2,4}[ -][A-Z0-9]{2,4}(?:[ -][A-Z0-9]{2,4})?)(?![A-Z0-9])'),
]
CODE_CONTEXT_RE = re.compile(
    r'(?:code|verification|verify|sign[ -]?in|login|log in|one[- ]?time|otp|2fa|two[- ]?factor|security code|authenticate|confirm|bekræft|password).{0,80}'
    r'|.{0,80}(?:code|verification|verify|sign[ -]?in|login|log in|one[- ]?time|otp|2fa|two[- ]?factor|security code|authenticate|confirm|bekræft|password)',
    re.I,
)
AUTH_HINT_RE = re.compile(r'(verification|verify|code|pin|sign[ -]?in|login|log in|one[- ]?time|otp|2fa|two[- ]?factor|security code|authenticate|password)', re.I)
FALSE_CODE_CONTEXT_RE = re.compile(r'(20[0-9]{2}|19[0-9]{2}).{0,35}(date|year|copyright|valid|expires|expiry|departure|arrival|booking|reservation|confirmed|created|updated)|(?:date|year|copyright|valid|expires|expiry|departure|arrival|booking|reservation|confirmed|created|updated).{0,35}(20[0-9]{2}|19[0-9]{2})', re.I)
MARKETING_CONTEXT_RE = re.compile(r'(coupon|promo|discount|sale|save|off|deal|voucher|loyalty|rabatt|tilbud)', re.I)
REJECT_WORDS = {'signin', 'login', 'verify', 'code', 'password', 'account', 'security', 'gmail', 'google', 'mozilla', 'github', 'support', 'june', 'email'}

BOOKING_DOMAINS = ('booking.com', 'airbnb.', 'hostelworld.', 'hotels.com', 'expedia.', 'agoda.', 'vrbo.', 'hostelbookers.', 'hotelplanner.', 'trip.com')
BOOKING_HINT_RE = re.compile(r'(booking|booked|reservation|confirmed|confirmation|check[- ]?in|hostel|hotel|apartment|stay|guest|property|accommodation|message from|special request)', re.I)
PIN_LABEL_RE = re.compile(r'(pin|entry code|access code|door code|key code|check[- ]?in code|booking pin)', re.I)
BOOKING_REF_RE = re.compile(r'(?:confirmation|booking|reservation|reference|itinerary)(?:\s+(?:number|no\.?|id|code))?\s*[:#]?\s*([A-Z0-9-]{6,16})', re.I)
BOOKING_SENDER_RE = re.compile(r'(booking\.com|airbnb\.|hostelworld\.|hotels\.com|expedia\.|agoda\.|vrbo\.|hostelbookers\.|hotelplanner\.|trip\.com)', re.I)
TRAVEL_SENDER_RE = re.compile(r'(interrail\.|eurail\.|ouigo\.|sncf\.|renfe\.|trainline\.|rail|pasngr\.ouigo)', re.I)
MARKETING_MAIL_RE = re.compile(r'(don.t miss out|hostels? you.ll love|recommended for you|inspiration|newsletter|sale|deal|discount|promo|special offer|save up to|explore|travel tips|where to stay)', re.I)
ACTIONABLE_BOOKING_RE = re.compile(r'(booking number|reservation number|confirmation number|reference|pin|entry code|access code|check[- ]?in|arrival time|payment|pay at|cash|deposit|property.s message|you have a message from|special request|ticket|e-ticket|attached|cancel(?:led|lation)?|changed|updated)', re.I)

NOISE_LINES_RE = re.compile(r'^(Booking\.com|Manage your booking|Contact Customer Service|Visit the Help Centre|Copyright|Get the app|Learn more|Open the|View |Modify your booking|QR code|Make your trip easy|Travel with peace of mind|Important Details)$', re.I)


def run(args, timeout=30):
    try:
        return subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, '', 'timeout')


def load_state():
    global _saved_text
    if STATE.exists():
        try:
            raw = STATE.read_text()
            state = json.loads(raw)
            if _saved_text is None:
                _saved_text = json.dumps(state, sort_keys=True) + '\n'
            state.setdefault('sent', [])
            state.setdefault('bookings', {})
            state.setdefault('travel', {})
            state.setdefault('retry_ids', [])
            return state
        except Exception:
            pass
    return {'last_seen_id': 0, 'sent': [], 'bookings': {}, 'travel': {}, 'retry_ids': [], 'last_bot_message_id': None}


def save_state(state):
    global _saved_text
    text = json.dumps(state, sort_keys=True) + '\n'
    if text == _saved_text:
        return
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(text)
    os.replace(tmp, STATE)
    _saved_text = text


def diablo_token():
    try:
        conn = sqlite3.connect(f'file:{DIABLO_HUB_DB}?mode=ro', uri=True, timeout=5)
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = 'telegram_bot_token'").fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    return row[0].strip() if row and row[0] and row[0].strip() else None


def env_token():
    for key in ('TELEGRAM_BOT_TOKEN', 'BOT_TOKEN', 'TELEGRAM_TOKEN'):
        if os.environ.get(key):
            return os.environ[key]
    token = diablo_token()
    if token:
        return token
    try:
        for line in HERMES_ENV.read_text(errors='ignore').splitlines():
            if '=' not in line or line.lstrip().startswith('#'):
                continue
            key, val = line.split('=', 1)
            if key.strip() in ('TELEGRAM_BOT_TOKEN', 'BOT_TOKEN', 'TELEGRAM_TOKEN'):
                return val.strip().strip('"\'')
    except Exception:
        pass
    return None


def telegram_api(method, data):
    token = env_token()
    if not token:
        return None
    encoded = urllib.parse.urlencode(data).encode()
    with urllib.request.urlopen(f'https://api.telegram.org/bot{token}/{method}', data=encoded, timeout=30) as response:
        return json.loads(response.read().decode())


def telegram_multipart(method, fields, file_field, file_path):
    token = env_token()
    if not token:
        return None
    boundary = '----HermesBoundary7MA4YWxkTrZu0gW'
    body = bytearray()
    for name, value in fields.items():
        if value is None:
            continue
        body.extend(f'--{boundary}\r\n'.encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(str(value).encode())
        body.extend(b'\r\n')
    path = Path(file_path)
    mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    body.extend(f'--{boundary}\r\n'.encode())
    body.extend(f'Content-Disposition: form-data; name="{file_field}"; filename="{path.name}"\r\n'.encode())
    body.extend(f'Content-Type: {mime}\r\n\r\n'.encode())
    body.extend(path.read_bytes())
    body.extend(b'\r\n')
    body.extend(f'--{boundary}--\r\n'.encode())
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/{method}',
        data=bytes(body),
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode())


def send_document(file_path, state, reply_to_message_id=None, caption=None):
    fields = {'chat_id': CHAT_ID}
    if caption:
        fields['caption'] = caption
        fields['parse_mode'] = 'HTML'
    if reply_to_message_id:
        fields['reply_to_message_id'] = str(reply_to_message_id)
        fields['allow_sending_without_reply'] = 'true'
    result = telegram_multipart('sendDocument', fields, 'document', file_path)
    mid = (result or {}).get('result', {}).get('message_id')
    if mid:
        state['last_bot_message_id'] = mid
    return mid


def send_telegram(text, state, reply_to_message_id=None):
    data = {'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': 'true'}
    if reply_to_message_id:
        data['reply_to_message_id'] = str(reply_to_message_id)
        data['allow_sending_without_reply'] = 'true'
    result = telegram_api('sendMessage', data)
    mid = (result or {}).get('result', {}).get('message_id')
    if mid:
        state['last_bot_message_id'] = mid
    return mid


def edit_telegram(message_id, text):
    try:
        return telegram_api('editMessageText', {
            'chat_id': CHAT_ID,
            'message_id': str(message_id),
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': 'true',
        })
    except Exception:
        # Old/non-editable Telegram messages should not block attachment delivery.
        return None


def htext(s):
    return html.escape(str(s), quote=False)


def hcode(s):
    return f'<code>{html.escape(str(s), quote=False)}</code>'


FRENCH_HINT_RE = re.compile(r"\b(le|la|les|des|une|vous|votre|vos|bonjour|arrivée|arrivee|départ|depart|réservation|reservation|paiement|espèces|especes|chambre|appartement|merci|cordialement|séjour|sejour|billet|train|gare|heure|jours?|requis|requise|obligatoire)\b", re.I)
SPANISH_HINT_RE = re.compile(r"\b(hola|como|cómo|van|preparativos|escribo|puedes|facilitar|vuestra|hora|llegada|saludo|gracias|antemano|reserva|reservación|apartamento|habitación|pago|efectivo|tarjeta|billete|estación)\b", re.I)

PHRASE_TRANSLATIONS = [
    (r"Bonjour", "Hello"),
    (r"Cher client", "Dear customer"),
    (r"Chère cliente", "Dear customer"),
    (r"Merci pour votre réservation", "Thank you for your booking"),
    (r"Votre réservation est confirmée", "Your booking is confirmed"),
    (r"Numéro de réservation", "Booking number"),
    (r"code PIN", "PIN code"),
    (r"heure d'arrivée", "arrival time"),
    (r"heure de départ", "departure time"),
    (r"arrivée", "arrival"),
    (r"départ", "departure"),
    (r"paiement en espèces", "cash payment"),
    (r"espèces", "cash"),
    (r"carte bancaire", "bank card"),
    (r"pièce jointe", "attachment"),
    (r"billets?", "tickets"),
    (r"e[- ]?billets?", "e-tickets"),
    (r"gare", "station"),
    (r"train", "train"),
    (r"Veuillez", "Please"),
    (r"s'il vous plaît", "please"),
    (r"merci", "thank you"),
    (r"cordialement", "best regards"),
    (r"Hola! Como van esos preparativos\? Te escribo para ver si me puedes facilitar mas o menos vuestra hora de llegada\. Un saludo y gracias de antemano\.", "Hi! How are preparations going? I am writing to ask if you can give me an approximate arrival time. Best regards and thank you in advance."),
    (r"Hola!?", "Hi!"),
    (r"C[oó]mo van esos preparativos\??", "How are preparations going?"),
    (r"Te escribo para ver si me puedes facilitar m[aá]s o menos vuestra hora de llegada\.?", "I am writing to ask if you can give me an approximate arrival time."),
    (r"Un saludo y gracias de antemano\.?", "Best regards and thank you in advance."),
    (r"hora de llegada", "arrival time"),
    (r"n[uú]mero de reserva", "booking number"),
    (r"reserva confirmada", "booking confirmed"),
    (r"pago en efectivo", "cash payment"),
    (r"tarjeta bancaria", "bank card"),
]

WORD_TRANSLATIONS = {
    'bonjour': 'hello', 'réservation': 'booking', 'reservation': 'booking', 'votre': 'your',
    'vos': 'your', 'vous': 'you', 'notre': 'our', 'nos': 'our', 'arrivée': 'arrival',
    'arrivee': 'arrival', 'départ': 'departure', 'depart': 'departure', 'paiement': 'payment',
    'espèces': 'cash', 'especes': 'cash', 'chambre': 'room', 'appartement': 'apartment',
    'séjour': 'stay', 'sejour': 'stay', 'billet': 'ticket', 'billets': 'tickets',
    'gare': 'station', 'train': 'train', 'merci': 'thank you', 'client': 'customer',
    'clients': 'customers', 'important': 'important', 'obligatoire': 'required',
    'annulation': 'cancellation', 'adresse': 'address', 'jour': 'day', 'jours': 'days',
    'heure': 'time', 'heures': 'hours', 'avant': 'before', 'après': 'after', 'apres': 'after',
    'requis': 'required', 'requise': 'required', 'obligatoire': 'required',
    'hola': 'hi', 'como': 'how', 'cómo': 'how', 'van': 'going', 'preparativos': 'preparations',
    'escribo': 'writing', 'puedes': 'can', 'facilitar': 'give', 'mas': 'more', 'más': 'more',
    'menos': 'less', 'vuestra': 'your', 'hora': 'time', 'llegada': 'arrival', 'saludo': 'regards',
    'gracias': 'thank you', 'antemano': 'advance', 'reserva': 'booking', 'reservación': 'booking',
    'apartamento': 'apartment', 'habitación': 'room', 'pago': 'payment', 'efectivo': 'cash',
    'tarjeta': 'card', 'billete': 'ticket', 'estación': 'station',
}


def translate_to_english(text):
    """Best-effort local translation for monitor alerts.

    The cron script must stay non-interactive and should not depend on paid model
    calls. This handles common French booking/travel mail phrases and labels so
    Telegram alerts are English by default.
    """
    if not text:
        return text
    out = html.unescape(str(text))
    for pat, repl in PHRASE_TRANSLATIONS:
        out = re.sub(pat, repl, out, flags=re.I)
    if FRENCH_HINT_RE.search(out) or SPANISH_HINT_RE.search(out):
        def repl_word(m):
            word = m.group(0)
            trans = WORD_TRANSLATIONS.get(word.lower())
            if not trans:
                return word
            return trans[:1].upper() + trans[1:] if word[:1].isupper() else trans
        out = re.sub(r"\b[A-Za-zÀ-ÿ']+\b", repl_word, out)
    out = re.sub(r'\s+', ' ', out).strip()
    return out


def section_title(n, title):
    return f'Section {n}: {title}'


def booking_notice(item, added_sections):
    place = item.get('place') or 'your stay'
    if added_sections:
        nums = ', '.join(str(n) for n in added_sections)
        word = 'section' if len(added_sections) == 1 else 'sections'
        return f'Updated booking at {htext(place)}. Added {word} {nums}.'
    return f'Updated booking at {htext(place)}.'


def travel_notice(item, added_sections):
    order = item.get('order')
    target = f'travel booking {order}' if order else 'travel booking'
    if added_sections:
        nums = ', '.join(str(n) for n in added_sections)
        word = 'section' if len(added_sections) == 1 else 'sections'
        return f'Updated {htext(target)}. Added {word} {nums}.'
    return f'Updated {htext(target)}.'


def normalize_code(raw):
    return re.sub(r'[ -]', '', raw.strip())


def plausible(raw):
    norm = normalize_code(raw)
    low = norm.lower()
    if low in REJECT_WORDS:
        return False
    if norm.isdigit():
        if re.fullmatch(r'(?:19|20)\d{2}', norm):
            return False
        return 4 <= len(norm) <= 8
    if not (5 <= len(norm) <= 10):
        return False
    letters = ''.join(ch for ch in raw if ch.isalpha())
    return bool(letters) and (letters.upper() == letters or raw.strip().replace('-', '').replace(' ', '').isalnum())


def extract_codes(subject, body):
    text = re.sub(r'\s+', ' ', f'{subject}\n{body}')
    found = []
    for rx in CODE_PATTERNS:
        for m in rx.finditer(text):
            raw = m.group(1) if m.groups() else m.group(0)
            if not plausible(raw):
                continue
            context = text[max(0, m.start() - 90):min(len(text), m.end() + 90)]
            if not CODE_CONTEXT_RE.search(context):
                continue
            code = normalize_code(raw)
            if re.fullmatch(r'(?:19|20)\d{2}', code) or FALSE_CODE_CONTEXT_RE.search(context):
                continue
            if MARKETING_CONTEXT_RE.search(context) and not AUTH_HINT_RE.search(context):
                continue
            if code not in found:
                found.append(code)
    return found


def service_name(env):
    frm = env.get('from') or {}
    name = frm.get('name') or ''
    addr = frm.get('addr') or ''
    subj = env.get('subject') or ''
    base = name or addr.split('@')[0] or 'your account'
    m = re.search(r'(?:sign in|login|verify|verification|code) (?:to|for) ([A-Za-z0-9 ._+-]{2,40})', subj, re.I)
    return (m.group(1) if m else base).strip(' .-') or 'your account'


def is_marketing_booking_mail(env, body):
    frm = env.get('from') or {}
    sender = f"{frm.get('name') or ''} {frm.get('addr') or ''}".lower()
    if not any(domain in sender for domain in BOOKING_DOMAINS):
        return False
    text = f"{env.get('subject') or ''}\n{body[:5000]}"
    return bool(MARKETING_MAIL_RE.search(text)) and not (
        ACTIONABLE_BOOKING_RE.search(text) or booking_reference_number(body) or attachment_names(body)
    )


def is_booking_mail(env, body):
    frm = env.get('from') or {}
    sender = f"{frm.get('name') or ''} {frm.get('addr') or ''}".lower()
    subject = env.get('subject') or ''
    if is_marketing_booking_mail(env, body):
        return False
    return any(domain in sender for domain in BOOKING_DOMAINS) and bool(BOOKING_HINT_RE.search(f'{subject}\n{body[:3000]}'))


def booking_sender_label(env):
    frm = env.get('from') or {}
    addr = (frm.get('addr') or '').lower()
    name = frm.get('name') or ''
    low_name = name.lower()
    if 'booking.com' in addr or 'booking.com' in low_name:
        return '@Booking.com'
    if 'airbnb' in addr or 'airbnb' in low_name:
        return '@Airbnb'
    if 'hostelworld' in addr or 'hostelworld' in low_name:
        return '@Hostelworld'
    if 'hotels.com' in addr or 'hotels.com' in low_name:
        return '@Hotels.com'
    return '@' + (addr.split('@')[-1] if '@' in addr else (name or 'booking'))


def booking_reference_number(body):
    text = re.sub(r'\s+', ' ', body)
    for m in BOOKING_REF_RE.finditer(text):
        value = m.group(1).strip(' .,-:')
        if not any(ch.isdigit() for ch in value):
            continue
        lowctx = text[max(0, m.start()-40):m.end()+40].lower()
        if 'pin' in lowctx and len(value) <= 5:
            continue
        if value.lower() in REJECT_WORDS:
            continue
        return value
    return None


def booking_property_name(env, body):
    subject = env.get('subject') or ''
    patterns = [
        r'(?:confirmed|reservation|booking).*? at ([^\n.!?]{3,90})',
        r'You have a message from ([^\n.!?]{3,90})',
        r'Special Request for your Reservation \d+.*?\n.*?from ([^\n.!?]{3,90})',
        r'at ([^\n.!?]{3,90})',
        r'Your (?:apartment|hotel|hostel|stay|booking|reservation) (?:in|at) ([^\n.!?]{3,90})',
    ]
    for source in (subject, body):
        for pat in patterns:
            m = re.search(pat, source, re.I | re.S)
            if m:
                name = re.sub(r'\s+', ' ', m.group(1)).strip(' .,-:')
                name = re.sub(r'\s+confirmed$', '', name, flags=re.I).strip(' .,-:')
                if 3 <= len(name) <= 90:
                    return translate_to_english(name)
    return 'your stay'


def booking_identity(env, body, code=None):
    frm = env.get('from') or {}
    sender = (frm.get('addr') or frm.get('name') or 'booking').lower()
    domain = sender.split('@')[-1] if '@' in sender else sender
    ref = booking_reference_number(body)
    if ref:
        return f'{domain}:ref:{ref}'
    place = booking_property_name(env, body).lower()
    place = re.sub(r'[^a-z0-9]+', '-', place).strip('-')[:60]
    return f'{domain}:place:{place}'


def choose_booking_pin(subject, body, codes):
    if not codes:
        return None
    text = re.sub(r'\s+', ' ', f'{subject}\n{body}')
    for code in codes:
        idx = text.find(code)
        if idx < 0:
            m = re.search(r'[ -]?'.join(map(re.escape, code)), text)
            idx = m.start() if m else -1
        context = text[max(0, idx - 80):idx + len(code) + 80] if idx >= 0 else ''
        if PIN_LABEL_RE.search(context):
            return code
    return codes[0]


def booking_code_label(body, code):
    text = re.sub(r'\s+', ' ', body)
    idx = text.find(code)
    if idx < 0:
        m = re.search(r'[ -]?'.join(map(re.escape, code)), text)
        idx = m.start() if m else -1
    context = text[max(0, idx - 80):idx + len(code) + 80] if idx >= 0 else text[:500]
    m = PIN_LABEL_RE.search(context)
    if not m:
        return 'PIN'
    label = m.group(1).lower().replace('-', ' ')
    return 'PIN' if label in ('pin', 'booking pin') else label


def clean_info_line(line):
    line = re.sub(r'https?://\S+', '', line)
    line = re.sub(r'\([^)]*booking\.com[^)]*\)', '', line, flags=re.I)
    line = re.sub(r'\s+', ' ', line).strip(' -*•\t;')
    return translate_to_english(line)


def extract_important_booking_info(env, body):
    subject = env.get('subject') or ''
    text = re.sub(r'\s+', ' ', body)

    m = re.search(r"Property's message:\s*(.*?)(?:view messages|Reservation Details|© Copyright|This email was sent)", text, re.I | re.S)
    if m:
        msg = re.sub(r'https?://\S+', '', m.group(1))
        candidates = []
        for part in re.split(r'(?:\s+-\s+|\r?\n+|Dear Customer,)', msg):
            line = clean_info_line(part)
            if len(line) >= 20 and not NOISE_LINES_RE.match(line):
                candidates.append(line)
        if candidates:
            return ' / '.join(candidates[:3])[:450]

    lines = []
    for raw in body.splitlines():
        line = clean_info_line(raw)
        if len(line) < 8 or NOISE_LINES_RE.match(line):
            continue
        if line.lower().startswith(('subject:', 'confirmation number:', 'booking number:')):
            continue
        if re.search(r'(requested|request|arranged|arrival|check[- ]?in|deposit|cash|keys|message|host|property|damage|cancel|payment|paid|required|please note|important|parking)', line, re.I):
            if line not in lines:
                lines.append(line)
        if len(lines) >= 3:
            break
    if lines:
        return ' / '.join(lines)[:450]
    return subject[:250]


def format_booking_text(env, body, code=None, info_items=None):
    place = translate_to_english(booking_property_name(env, body))
    ref = booking_reference_number(body)
    lines = [
        f'<b>Mail from {htext(booking_sender_label(env))}</b>',
        f'Booking at <b>{htext(place)}</b> confirmed.',
    ]
    details = []
    if ref:
        details.append(f'Booking number: {hcode(ref)}')
    if code:
        details.append(f'{htext(booking_code_label(body, code))}: {hcode(code)}')
    section_no = 1
    if details:
        lines.append(f'\n<b>{section_title(section_no, "Details")}</b>')
        lines.extend(f'• {d}' for d in details)
        section_no += 1
    for idx, item in enumerate(info_items or [], section_no):
        lines.append(f'\n<b>{section_title(idx, "Update")}</b>')
        lines.append(f'• {htext(translate_to_english(item))}')
    return '\n'.join(lines)


def attachment_names(body):
    names = []
    for m in re.finditer(r'<#part\s+type=([^\s>]+)\s+filename="([^"]+)"', body):
        fn = Path(m.group(2)).name
        if fn not in names:
            names.append(fn)
    return names


def download_attachments(mid):
    out_dir = ATTACH_DIR / str(mid)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Use a clean per-message directory. Himalaya otherwise creates *_1.pdf on
    # repeated downloads, which can make the monitor send duplicate documents.
    for old in out_dir.iterdir():
        if old.is_file():
            try:
                old.unlink()
            except Exception:
                pass
    r = run([HIMALAYA, 'attachment', 'download', '--downloads-dir', str(out_dir), str(mid), '--output', 'json'], timeout=90)
    if r.returncode != 0:
        return {}
    files = {}
    for p in out_dir.iterdir():
        if p.is_file() and p.stat().st_size > 0:
            name = re.sub(r'_(\d+)(\.[^.]+)$', r'\2', p.name)
            target = out_dir / name
            if target != p:
                if target.exists():
                    p.unlink()
                    continue
                p.rename(target)
                p = target
            files[p.name] = str(p)
    return files


def send_new_attachment_files(item, state, reply_to_message_id):
    sent_docs = set(item.get('sent_documents') or [])
    sent_any = False
    for name, path in sorted((item.get('attachment_paths') or {}).items()):
        if name in sent_docs:
            continue
        if not Path(path).is_file() or Path(path).stat().st_size <= 0:
            continue
        send_document(path, state, reply_to_message_id=reply_to_message_id)
        sent_docs.add(name)
        sent_any = True
    item['sent_documents'] = sorted(sent_docs)
    return sent_any


def travel_sender_label(env):
    frm = env.get('from') or {}
    addr = (frm.get('addr') or '').lower()
    name = frm.get('name') or ''
    low_name = name.lower()
    if 'interrail' in addr or 'interrail' in low_name:
        return '@Interrail'
    if 'ouigo' in addr or 'ouigo' in low_name or 'pasngr.ouigo' in addr:
        return '@OUIGO'
    if 'sncf' in addr or 'sncf' in low_name:
        return '@SNCF'
    return '@' + (addr.split('@')[-1] if '@' in addr else (name or 'travel'))


def is_travel_mail(env, body):
    frm = env.get('from') or {}
    hay = f"{env.get('subject') or ''} {frm.get('name') or ''} {frm.get('addr') or ''} {body[:1500]}"
    return bool(TRAVEL_SENDER_RE.search(hay)) and bool(re.search(r'(ticket|e-ticket|reservation|train|order number|booking number|seat reservation|billet|trajet)', hay, re.I))


def travel_order_number(body):
    text = re.sub(r'\s+', ' ', body)
    for pat in [r'order number is\s*([A-Z0-9-]{6,16})', r'order\s+number\s*[:#]?\s*([A-Z0-9-]{6,16})', r'booking number is\s*[:#]?\s*([A-Z0-9-]{5,16})', r'numéro de réservation est\s*:?\s*([A-Z0-9-]{5,16})']:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1).strip()
    return None


def travel_identity(env, body):
    order = travel_order_number(body)
    frm = env.get('from') or {}
    sender = (frm.get('addr') or frm.get('name') or 'travel').lower()
    domain = sender.split('@')[-1] if '@' in sender else sender
    if order:
        # Group OUIGO and Interrail emails for the same train when possible.
        return f'travel:{order}' if order.startswith('R') else f'{domain}:travel:{order}'
    subj = re.sub(r'[^a-z0-9]+', '-', (env.get('subject') or 'travel').lower()).strip('-')[:70]
    return f'{domain}:travel:{subj}'


def extract_ticket_numbers(body):
    text = re.sub(r'\s+', ' ', body)
    found = []
    for pat in [r'Ticket number:\s*([A-Z0-9-]{5,12})']:
        for m in re.finditer(pat, text, re.I):
            val = m.group(1).strip()
            if val not in found:
                found.append(val)
    return found


def extract_travel_segments(body):
    text = re.sub(r'\s+', ' ', body)
    segs = []
    # Interrail order lines
    for m in re.finditer(r'([A-Z][A-Za-zÀ-ÿ .-]+ \([^)]*\))\s*-\s*([A-Z][A-Za-zÀ-ÿ .-]+ \([^)]*\))\s*(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+([0-9]{2} [A-Za-z]+ 20[0-9]{2}),\s*([0-9]{2}:[0-9]{2}),\s*([^\n]*?class)\s+Ticket number:\s*([A-Z0-9-]{5,12})', text, re.I):
        origin = re.sub(r'^(?:EUR|USD|DKK)\s+', '', m.group(1)).strip()
        dest = re.sub(r'^(?:EUR|USD|DKK)\s+', '', m.group(2)).strip()
        seg = f"{origin} → {dest}, {m.group(3)} {m.group(4)} {m.group(5)}, {m.group(6)}"
        if seg not in segs:
            segs.append(seg)
    # OUIGO compact line
    m = re.search(r'([0-9]{2}h[0-9]{2})\s+Lyon Perrache\s+([0-9]{2}h[0-9]{2})\s+Valence TGV Rhône Alpes Sud.*?TRAIN N[°o]\s*([0-9]{3,6})', text, re.I)
    if m:
        segs.append(f"Lyon Perrache → Valence TGV Rhône Alpes Sud, 03/08/2026 {m.group(1).replace('h', ':')}–{m.group(2).replace('h', ':')}, train {m.group(3)}")
    return segs[:5]


def extract_travel_info(env, body):
    text = re.sub(r'\s+', ' ', body)
    infos = []
    if re.search(r'e-tickets are attached|Important documents attached|available as an attachment|pièce jointe', text, re.I):
        infos.append('Tickets/e-tickets are attached; this email alone is not enough.')
    if re.search(r'print them|digital or printed copy|impression', text, re.I):
        infos.append('Bring a digital or printed copy of the tickets, plus your Interrail Pass.')
    if re.search(r'5 minutes before departure|5 minutes avant le départ', text, re.I):
        infos.append('Be at the platform at least 5 minutes before departure.')
    if re.search(r'activate a travel day|add it to your Pass', text, re.I):
        infos.append('Add the journey to your Interrail Pass and activate the travel day in Rail Planner.')
    return infos[:4]


def format_travel_text(env, body, item):
    lines = [f'<b>Mail from {htext(travel_sender_label(env))}</b>']
    order = item.get('order') or travel_order_number(body)
    if order:
        label = 'Order' if order.startswith('R') else 'Booking'
        lines.append(f'• {label}: {hcode(order)}')

    section_no = 1
    segments = item.get('segments') or []
    if segments:
        lines.append(f'\n<b>{section_title(section_no, "Trains")}</b>')
        lines.extend(f'{i}. {htext(translate_to_english(seg))}' for i, seg in enumerate(segments, 1))
        section_no += 1

    tickets = item.get('tickets') or []
    if tickets:
        lines.append(f'\n<b>{section_title(section_no, "Tickets")}</b>')
        lines.append('• Numbers: ' + ', '.join(hcode(x) for x in tickets))
        section_no += 1

    attachments = item.get('attachments') or []
    if attachments:
        lines.append(f'\n<b>{section_title(section_no, "Attached files")}</b>')
        lines.extend(f'• {htext(x)}' for x in attachments)
        section_no += 1

    info = item.get('info') or []
    if info:
        lines.append(f'\n<b>{section_title(section_no, "Important")}</b>')
        lines.extend(f'• {htext(translate_to_english(x))}' for x in info)
    return '\n'.join(lines)


def deliver_travel(env, body, state, mid=None):
    travel = state.setdefault('travel', {})
    tid = travel_identity(env, body)
    item = travel.setdefault(tid, {})
    changed = False
    order = travel_order_number(body)
    if order and item.get('order') != order:
        item['order'] = order; changed = True
    downloaded = download_attachments(mid) if mid and attachment_names(body) else {}
    if downloaded:
        existing_paths = item.get('attachment_paths', {})
        if any(name not in existing_paths for name in downloaded):
            changed = True
        item['attachment_paths'] = {**existing_paths, **downloaded}
    actual_attachments = sorted((item.get('attachment_paths') or {}).keys())
    before_counts = {field: len(item.get(field) or []) for field in ('segments', 'tickets', 'attachments', 'info')}
    for field, values in [('segments', extract_travel_segments(body)), ('tickets', extract_ticket_numbers(body)), ('attachments', actual_attachments), ('info', extract_travel_info(env, body))]:
        cur = item.setdefault(field, [])
        for value in values:
            if value not in cur:
                cur.append(value); changed = True
        item[field] = cur[-8:]
    added_sections = []
    section_map = []
    if item.get('segments'):
        section_map.append('segments')
    if item.get('tickets'):
        section_map.append('tickets')
    if item.get('attachments'):
        section_map.append('attachments')
    if item.get('info'):
        section_map.append('info')
    for idx, field in enumerate(section_map, 1):
        if len(item.get(field) or []) > before_counts.get(field, 0):
            added_sections.append(idx)
    item['added_sections'] = added_sections
    text = format_travel_text(env, body, item)
    if text != item.get('last_text'):
        changed = True
    if not changed:
        return
    item['last_text'] = text
    msg_id = item.get('message_id')
    if msg_id:
        edit_result = edit_telegram(msg_id, text)
        if edit_result and state.get('last_bot_message_id') and state.get('last_bot_message_id') != msg_id:
            item['last_update_notice_id'] = send_telegram(travel_notice(item, item.get('added_sections') or []), state, reply_to_message_id=msg_id)
    else:
        msg_id = send_telegram(text, state)
        if msg_id:
            item['message_id'] = msg_id
    if msg_id:
        send_new_attachment_files(item, state, msg_id)
    travel[tid] = item


def format_auth_text(env, code):
    return f'Your verification code for {htext(service_name(env))} is: {hcode(code)}'


def remember_or_update_booking(env, body, state, code=None, info=None):
    bookings = state.setdefault('bookings', {})
    bid = booking_identity(env, body, code)
    item = bookings.setdefault(bid, {})

    # If this has a ref and an older same-place item existed without ref, merge into it.
    place = booking_property_name(env, body)
    ref = booking_reference_number(body)
    if ref:
        same_place_key = None
        for k, v in bookings.items():
            if k != bid and v.get('place') == place and not v.get('ref'):
                same_place_key = k
                break
        if same_place_key:
            old = bookings.pop(same_place_key)
            old.update(item)
            item = bookings.setdefault(bid, old)

    changed = False
    place = translate_to_english(place)
    if place and item.get('place') != place:
        item['place'] = place
        changed = True
    if ref and item.get('ref') != ref:
        item['ref'] = ref
        changed = True
    if code and item.get('pin') != code:
        item['pin'] = code
        changed = True
    added_sections = []
    if info:
        info = translate_to_english(info)
        infos = item.setdefault('info', [])
        if info not in infos:
            infos.append(info)
            item['info'] = infos[-5:]
            # Section 1 is Details when booking number/PIN exists; update
            # sections follow in the same order as item['info'].
            base = 1 if (item.get('ref') or item.get('pin')) else 0
            added_sections.append(base + len(item['info']))
            changed = True
    item['added_sections'] = added_sections

    text = format_booking_text(env, body, item.get('pin'), item.get('info'))
    old_text = item.get('last_text')
    item['last_text'] = text
    bookings[bid] = item
    return bid, item, changed or text != old_text


def deliver_booking(env, body, state, code=None, info=None):
    bid, item, changed = remember_or_update_booking(env, body, state, code=code, info=info)
    if not changed:
        return
    msg_id = item.get('message_id')
    text = item['last_text']
    if msg_id:
        edit_result = edit_telegram(msg_id, text)
        if edit_result and state.get('last_bot_message_id') and state.get('last_bot_message_id') != msg_id:
            notify_text = booking_notice(item, item.get('added_sections') or [])
            notify = send_telegram(notify_text, state, reply_to_message_id=msg_id)
            item['last_update_notice_id'] = notify
    else:
        msg_id = send_telegram(text, state)
        if msg_id:
            item['message_id'] = msg_id
    state.setdefault('bookings', {})[bid] = item


def deliver_auth(env, code, state):
    send_telegram(format_auth_text(env, code), state)


def process_env(env, state, sent):
    mid = int(env.get('id') or 0)
    subject = env.get('subject') or ''
    frm = env.get('from') or {}
    haystack = f"{subject} {frm.get('name') or ''} {frm.get('addr') or ''}"
    booking_sender = bool(BOOKING_SENDER_RE.search(haystack))
    # Cheap envelope filter: non-booking mail needs an actual auth hint.
    # Avoid reading bodies just because a marketing/booking subject says "confirmed".
    if booking_sender:
        if not HINT_RE.search(haystack):
            return 'done'
    elif not AUTH_HINT_RE.search(haystack):
        return 'done'

    rr = run([HIMALAYA, 'message', 'read', str(mid)], timeout=25)
    if rr.returncode != 0:
        return 'retry'
    body = rr.stdout
    if is_marketing_booking_mail(env, body):
        sent.add(f'marketing-message:{mid}')
        return 'done'
    codes = extract_codes(subject, body)

    if is_travel_mail(env, body):
        key = f'travel-message:{mid}'
        if key in sent:
            return 'done'
        sent.add(key)
        deliver_travel(env, body, state, mid=mid)
        return 'done'

    if is_booking_mail(env, body):
        code = choose_booking_pin(subject, body, codes)
        info = None if code else extract_important_booking_info(env, body)
        key = f'booking-message:{mid}'
        if key in sent:
            return 'done'
        sent.add(key)
        deliver_booking(env, body, state, code=code, info=info)
        return 'done'

    if not codes:
        return 'done'
    code = codes[0]
    key = f'{mid}:{code}'
    if key in sent:
        return
    sent.add(key)
    deliver_auth(env, code, state)
    return 'done'


def run_once():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a') as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return

        state = load_state()
        last_seen = int(state.get('last_seen_id') or 0)
        sent = set(state.get('sent') or [])
        retry_ids = {int(x) for x in state.get('retry_ids') or [] if str(x).isdigit()}

        r = run([HIMALAYA, 'envelope', 'list', '--page-size', '30', '--output', 'json'], timeout=20)
        if r.returncode != 0:
            return
        try:
            envs = json.loads(r.stdout)
        except Exception:
            return

        max_id = last_seen
        for env in sorted(envs, key=lambda e: int(e.get('id') or 0)):
            try:
                mid = int(env.get('id') or 0)
            except Exception:
                continue
            if mid <= last_seen and mid not in retry_ids:
                continue
            result = process_env(env, state, sent)
            if result == 'retry':
                retry_ids.add(mid)
            else:
                retry_ids.discard(mid)
                max_id = max(max_id, mid)

        state['last_seen_id'] = max_id
        state['retry_ids'] = sorted(retry_ids)[-20:]
        state['sent'] = sorted(sent)[-300:]
        save_state(state)


def main():
    if '--loop' in sys.argv:
        idx = sys.argv.index('--loop')
        try:
            interval = max(2.0, float(sys.argv[idx + 1]))
        except Exception:
            interval = 5.0
        while True:
            start = time.monotonic()
            run_once()
            elapsed = time.monotonic() - start
            time.sleep(max(0.5, interval - elapsed))
    else:
        run_once()


if __name__ == '__main__':
    main()
