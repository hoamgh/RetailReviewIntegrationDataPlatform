from experiments.tiktok_access_benchmark.network import diagnostic, decision, clean

def test_unknown_fetch_is_not_detail_denial():
    e=diagnostic({'url':'https://www.tiktok.com/api/unknown/?token=secret','status':403},'Fetch','NAVIGATION',{'method':'POST'},{'type':'script'})
    assert e['pathname']=='/api/unknown/' and e['hostname']=='www.tiktok.com'
    assert e['method']=='POST' and decision([e],True)=='UNKNOWN_403'

def test_required_denial_and_retry():
    e=diagnostic({'url':'https://www.tiktok.com/api/comment/list/','status':403},'Fetch','NAVIGATION')
    assert decision([e],True)=='ACCESS_DENIED'
    e['status']=429
    assert decision([e],True)=='RATE_LIMITED'

def test_static_requires_body_and_render_evidence():
    e=diagnostic({'url':'https://static.tiktokcdn.com/font.woff','status':403},'Font','NAVIGATION')
    assert decision([e],True)=='UNKNOWN_403'
    e.update(body_state='CAPTURED',body_excerpt='resource unavailable')
    assert decision([e],True) is None
    assert decision([e],False)=='UNKNOWN_403'
    e['body_excerpt']='security challenge'
    assert decision([e],True)=='CHALLENGE'

def test_sensitive_body_scrubbed():
    text=clean('<script>cookie=secret</script> Access Denied token=hidden https://example.com/error?token=hidden')
    assert 'Access Denied' in text and 'hidden' not in text and 'secret' not in text

def test_video_path_preserved_and_truncated_script_removed():
    path='/@a_public_username_longer_than_ten/video/7565877317415816466'
    e=diagnostic({'url':'https://www.tiktok.com'+path,'status':403},'Document','NAVIGATION')
    assert e['pathname']==path
    assert clean('Access Denied<script>const session=hidden')=='Access Denied'


def test_optional_related_items_403_does_not_prove_comment_denial():
    e=diagnostic({'url':'https://www.tiktok.com/api/related/item_list/','status':403},'Fetch','NAVIGATION')
    assert e['endpoint_kind']=='related_items_api'
    assert decision([e],True) is None
    assert decision([e],False)=='UNKNOWN_403'
    e['status']=429
    assert decision([e],True)=='RATE_LIMITED'
    e['status']=401
    assert decision([e],True)=='AUTH_GATE'


def test_optional_error_never_masks_required_denial_or_captcha():
    optional=diagnostic({'url':'https://www.tiktok.com/api/related/item_list/','status':403},'Fetch','NAVIGATION')
    required=diagnostic({'url':'https://www.tiktok.com/api/comment/list/','status':403},'Fetch','NAVIGATION')
    assert decision([optional,required],True)=='ACCESS_DENIED'
    doc=diagnostic({'url':'https://www.tiktok.com/api/related/item_list/','status':403},'Document','NAVIGATION')
    assert decision([doc],True)=='ACCESS_DENIED'
    optional['body_excerpt']='verify you are human'
    assert decision([optional],True)=='CHALLENGE'
