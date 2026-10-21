"""Shared TikTok dialog classification and bounded search/HTTP diagnostics."""
from __future__ import annotations
import base64
import json
import math
import time
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

def classify_dialog(dialog):
    """Classify structural evidence, never arbitrary role=dialog or comment text."""
    if dialog.get('challenge_marker'):
        return 'CHALLENGE'
    if dialog.get('login_marker') or dialog.get('auth_form'):
        return 'LOGIN_GATE'
    if dialog.get('consent_marker'):
        return 'CONSENT_GATE'
    if dialog.get('comment_panel'):
        return 'COMMENT_PANEL'
    if dialog.get('share_marker'):
        return 'SHARE_DIALOG'
    return 'UNKNOWN_DIALOG'

def dialog_kinds(dialogs):
    return [classify_dialog(d) for d in dialogs if isinstance(d, dict) and d.get('visible', True)]

def automatic_interstitial(snapshot):
    """Known noninteractive Please-wait page, not a CAPTCHA or proof of denial."""
    return (set(snapshot.get('challenge_markers') or [])=={'._wafchallengeid'}
        and set(snapshot.get('challenge_text_signals') or [])=={'please wait'}
        and not any(snapshot.get(k) for k in ('homepage','search','video_detail','comments')))

def retry_after_seconds(value, now=None):
    if value is None:
        return None
    value = str(value).strip()
    if value.isdigit():
        return int(value)  # Never shorten the server-provided cooldown.
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        now = now or datetime.now(timezone.utc)
        return max(0, math.ceil((date-now).total_seconds()))
    except (ValueError, TypeError, OverflowError):
        return None

def validate_search_body(body, http_status):
    """Read an already observed response, projecting no captions, IDs or tokens."""
    result = {'http_status': http_status, 'body_state': 'UNAVAILABLE',
              'application_status': None, 'application_error': False,
              'item_count': None, 'video_count': None, 'body_format':'UNKNOWN', 'body_bytes':None}
    content = body.get('body') if isinstance(body, dict) else None
    if not isinstance(content, str):
        return result
    # Match the existing place-discovery 2 MB cap, including decoded byte length.
    if len(content) > 2_000_000:
        return dict(result, body_state='BODY_LIMIT')
    try:
        raw = base64.b64decode(content, validate=True) if body.get('base64Encoded') else content.encode('utf-8')
        result['body_bytes'] = len(raw)
        if len(raw) > 2_000_000:
            return dict(result, body_state='BODY_LIMIT')
        stripped = raw.lstrip()
        result['body_format'] = ('EMPTY' if not stripped else 'HTML' if stripped.lower().startswith((b'<!doctype html',b'<html'))
            else 'JSON_LIKE' if stripped.startswith((b'{',b'[')) else 'OTHER_TEXT')
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        return dict(result, body_state='INVALID_JSON')
    result['body_format'] = 'JSON'
    if not isinstance(payload, dict):
        return dict(result, body_state='INVALID_SCHEMA')
    code = payload.get('status_code')
    if isinstance(code, str) and code.lstrip('-').isdigit():
        code = int(code)
    if isinstance(code, bool) or not isinstance(code, int):
        code = None
    result.update(application_status=code, application_error=code is not None and code != 0)
    data = payload.get('data')
    if not isinstance(data, list):
        return dict(result, body_state='INVALID_SCHEMA')
    items = [r['item'] for r in data if isinstance(r, dict) and isinstance(r.get('item'), dict)]
    result.update(body_state='VALID_JSON', item_count=len(items),
                  video_count=sum(bool(str(item.get('id') or '').isdigit()) for item in items))
    return result

def search_reason(responses, dom_ready):
    if not responses:
        return 'SEARCH_RESPONSE_NOT_OBSERVED'
    if any(r.get('application_error') for r in responses):
        return 'SEARCH_APPLICATION_ERROR'
    validated = [r for r in responses if r.get('body_state') == 'VALID_JSON'
                 and r.get('http_status') == 200 and r.get('application_status') == 0]
    if not validated:
        if any(r.get('body_format')=='EMPTY' for r in responses):
            return 'SEARCH_EMPTY_BODY'
        if any(r.get('body_format')=='HTML' for r in responses):
            return 'SEARCH_HTML_INSTEAD_OF_JSON'
        if any(r.get('body_state')=='INVALID_JSON' for r in responses):
            return 'SEARCH_INVALID_JSON'
        return 'SEARCH_RESPONSE_NOT_VALIDATED'
    if any(r.get('video_count', 0) > 0 for r in validated):
        return 'SEARCH_VIDEOS_AND_DOM_READY' if dom_ready else 'SEARCH_VIDEOS_DOM_NOT_RENDERED'
    return 'SEARCH_ZERO_VIDEOS'

def record_cooldown(path, seconds, now=None):
    if seconds is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time() if now is None else now
    until = now+seconds
    if path.exists():
        until = max(until, json.loads(path.read_text(encoding='utf-8'))['not_before_epoch'])
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'not_before_epoch':until,'reason':'HTTP_429_RETRY_AFTER'}),encoding='utf-8')
    temporary.replace(path)

def remaining_cooldown(path, now=None):
    path = Path(path)
    if not path.exists():
        return 0
    value = json.loads(path.read_text(encoding='utf-8'))['not_before_epoch']
    if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value):
        raise ValueError('Invalid persisted cooldown; live run refused')
    return max(0,math.ceil(value-(time.time() if now is None else now)))
