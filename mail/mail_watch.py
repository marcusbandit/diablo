#!/usr/bin/env python3
"""Watch one himalaya account for mails matching rules and ping Telegram.

Rules live in ~/.config/mail-watch/rules.json as a list of objects:
  {"name": "...", "from": "regex", "subject": "regex", "body": "regex", "any": "regex"}
Every field but name is optional; all given fields must match, case-insensitive.
"any" matches against sender, subject and body together.
"""
import html
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mail_code_monitor import CHAT_ID, telegram_api  # noqa: E402

HIMALAYA = '/usr/local/bin/himalaya'
RULES = Path.home() / '.config/mail-watch/rules.json'
STATE_DIR = Path.home() / '.local/state/mail-watch'


def himalaya(*args, timeout=60):
    out = subprocess.run([HIMALAYA, *args], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[-300:])
    return out.stdout


def envelopes(account):
    return json.loads(himalaya('envelope', 'list', '-a', account, '--page-size', '30', '--output', 'json'))


def body_of(account, mid):
    return himalaya('message', 'read', '-a', account, '--preview', '--no-headers', mid)


def matches(rule, env, account, mid):
    frm = env.get('from') or {}
    sender = f"{frm.get('name') or ''} <{frm.get('addr') or ''}>"
    if rule.get('from') and not re.search(rule['from'], sender, re.I):
        return False
    if rule.get('subject') and not re.search(rule['subject'], env.get('subject') or '', re.I):
        return False
    if rule.get('any'):
        text = f"{sender}\n{env.get('subject') or ''}"
        if not re.search(rule['any'], text, re.I) and not re.search(rule['any'], body_of(account, mid), re.I | re.S):
            return False
    if rule.get('body') and not re.search(rule['body'], body_of(account, mid), re.I | re.S):
        return False
    return True


def notify(account_email, rule, env):
    frm = env.get('from') or {}
    sender = frm.get('name') or frm.get('addr') or 'unknown'
    text = (
        f"<b>{html.escape(rule.get('name') or 'Mail')}</b> on {html.escape(account_email)}\n"
        f"From: {html.escape(sender)} &lt;{html.escape(frm.get('addr') or '')}&gt;\n"
        f"Subject: {html.escape(env.get('subject') or '(no subject)')}"
    )
    telegram_api('sendMessage', {'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'})


def main():
    account = sys.argv[1] if len(sys.argv) > 1 else 'rosado'
    account_email = sys.argv[2] if len(sys.argv) > 2 else account
    interval = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file = STATE_DIR / f'{account}.json'
    last = json.loads(state_file.read_text())['last_seen_id'] if state_file.exists() else None
    while True:
        try:
            rules = json.loads(RULES.read_text()) if RULES.exists() else []
            envs = envelopes(account)
            ids = [int(e['id']) for e in envs]
            if last is None:
                last = max(ids, default=0)
            for env in sorted(envs, key=lambda e: int(e['id'])):
                mid = int(env['id'])
                if mid <= last:
                    continue
                for rule in rules:
                    if matches(rule, env, account, str(mid)):
                        notify(account_email, rule, env)
                        break
                last = mid
            state_file.write_text(json.dumps({'last_seen_id': last}))
        except Exception as exc:
            print(f'mail-watch: {exc}', file=sys.stderr, flush=True)
        time.sleep(interval)


if __name__ == '__main__':
    main()
