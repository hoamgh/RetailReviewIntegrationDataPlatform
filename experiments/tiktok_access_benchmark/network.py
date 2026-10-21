"""Privacy-preserving browser response evidence; no request replay."""
import re
from urllib.parse import urlparse

def clean(value, limit=2048):
    text = str(value or '')
    text = re.sub(r'(?is)<(script|style)[^>]*>.*?(?:</\1>|$)', '', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'https?://[^\s"<>]+', lambda m: m.group(0).split('?')[0], text)
    text = re.sub(r'(?i)(token|cookie|authorization|session|signature|password|secret|msToken|X-Bogus)[\w-]*[\s"\x27:=]+[^\s,;<>]+', r'\1=[redacted]', text)
    text = re.sub(r'[A-Za-z0-9_+/=-]{40,}', '[redacted]', text)
    return text[:limit]

def diagnostic(response, resource_type, phase, request=None, initiator=None):
    url = urlparse(response.get('url', ''))
    host = url.hostname or ''
    path = url.path
    known = {'/api/search/general/full/':'search_api', '/api/comment/list/':'comments_api', '/api/item/detail/':'video_detail_api', '/api/related/item_list/':'related_items_api'}
    trusted = host == 'tiktok.com' or host.endswith('.tiktok.com')
    kind = known.get(path, 'unknown') if trusted else 'unknown'
    if resource_type == 'Document': kind = 'document'
    if resource_type in ('Media','Image','Font','Stylesheet','Script') and kind == 'unknown':
        kind = 'media' if resource_type in ('Media','Image') else 'ancillary'
    headers = {str(k).lower():v for k,v in response.get('headers', {}).items()}
    from experiments.tiktok_access_benchmark.access import retry_after_seconds
    source = (initiator or {}).get('url', '')
    frames = (initiator or {}).get('stack',{}).get('callFrames',[])
    if not source and frames: source = frames[0].get('url','')
    parsed_source = urlparse(source)
    safe_path = '/'.join(clean(segment) for segment in path.split('/'))
    safe_path = re.sub(r'(?i)(/(?:token|session|signature|secret|password)/)[^/]+',r'\1[redacted]',safe_path)
    return dict(status=int(response.get('status',0)), hostname=host, pathname=safe_path, path=safe_path,
        method=(request or {}).get('method','UNKNOWN'), resource_type=resource_type, phase=phase,
        endpoint_kind=kind, host_scope='tiktok' if trusted else 'other',
        initiator={'type':(initiator or {}).get('type','unknown'), 'hostname':parsed_source.hostname,
                   'pathname':'/'.join(clean(segment) for segment in parsed_source.path.split('/'))}, content_type=clean(headers.get('content-type',response.get('mimeType','')),128),
        body_state='NOT_CAPTURED', body_excerpt=None,
        retry_after_seconds=retry_after_seconds(headers.get('retry-after')) if int(response.get('status',0))==429 else None)

def decision(errors, rendered=False):
    for entry in errors:
        status = entry.get('status')
        body = (entry.get('body_excerpt') or '').lower()
        if status == 429: return 'RATE_LIMITED'
        if status not in (401,403): continue
        if any(s in body for s in ('captcha','verify you are human','security challenge')): return 'CHALLENGE'
        if entry.get('endpoint_kind') in ('document','search_api','video_detail_api','comments_api'):
            return 'AUTH_GATE' if status==401 else 'ACCESS_DENIED'
        # Exact optional recommendations endpoint; keep its 403 as telemetry.
        if (status == 403 and entry.get('endpoint_kind') == 'related_items_api' and rendered
                and not any(s in body for s in ('access denied', 'permission', 'login', 'authorize', 'forbidden'))):
            continue
        # Static resource classification alone cannot establish that an authorization check is optional.
        host = entry.get('hostname','')
        static_host = any(host.endswith('.'+suffix) for suffix in ('tiktokcdn.com','tiktokcdn-us.com','byteoversea.com'))
        if static_host and entry.get('endpoint_kind') in ('media','ancillary') and rendered and entry.get('body_state')=='CAPTURED' and bool(body.strip()) and not any(s in body for s in ('access denied','permission','challenge','login','authorize','forbidden')):
            continue
        return 'UNKNOWN_403' if status==403 else 'AUTH_GATE'
    return None
