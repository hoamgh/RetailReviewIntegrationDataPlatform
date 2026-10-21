() => {
  const visible = e => {
    if (!e || !e.getClientRects().length) return false;
    const s = getComputedStyle(e);
    if (s.visibility === 'hidden' || s.display === 'none' || s.opacity === '0') return false;
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const anyVisible = selector => Array.from(document.querySelectorAll(selector)).some(visible);
  const challengeSelector = '#captcha_container,.captcha-verify-container,._wafchallengeid,[id*="captcha-verify"]';
  const challengeMarkers = ['#captcha_container', '.captcha-verify-container', '._wafchallengeid', '[id*="captcha-verify"]']
    .filter(anyVisible);
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"],[data-e2e="login-modal"],#login-modal'))
    .filter(visible).slice(0, 10).map(e => {
      const structural = node => {
        const style = getComputedStyle(node), rect = node.getBoundingClientRect();
        const safe = value => /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(value || '') ? value : null;
        const tag = node.tagName.toLowerCase();
        const id = safe(node.id), marker = safe(node.getAttribute('data-e2e'));
        const selector = id ? `${tag}#${id}` : marker ? `${tag}[data-e2e="${marker}"]` : tag;
        return {selector,tag,role:node.getAttribute('role'),aria_modal:node.getAttribute('aria-modal'),
          visible:visible(node),display:style.display,visibility:style.visibility,opacity:style.opacity,
          rect:{left:Math.round(rect.left),top:Math.round(rect.top),width:Math.round(rect.width),height:Math.round(rect.height)}};
      };
      const ancestors = []; let parent = e.parentElement;
      for (let i=0;parent&&i<4;i++,parent=parent.parentElement) ancestors.push(structural(parent));
      const center = document.querySelector('[data-e2e="comment-icon"]');
      const targetRect = center?.getBoundingClientRect();
      const atTarget = targetRect && document.elementFromPoint(targetRect.left+targetRect.width/2,targetRect.top+targetRect.height/2);
      const targetBlocked = Boolean(atTarget && (e===atTarget || e.contains(atTarget)) && !center.contains(atTarget));
      const matches = selector => (e.matches(selector) && visible(e)) || Array.from(e.querySelectorAll(selector)).some(visible);
      const heading = Array.from(e.querySelectorAll('h1,h2,h3,[role="heading"]')).filter(visible)
        .map(h => (h.innerText || '').trim().toLowerCase());
      return {
        visible: true,
        diagnostic:{element:structural(e),ancestors,comment_center_covered:targetBlocked,
          sanitized_excerpt:[structural(e),...ancestors].map(n=>`<${n.tag}${n.role ? ' role=dialog' : ''}${n.aria_modal === 'true' ? ' aria-modal=true' : ''}>`).join(' > ').slice(0,320)},
        login_marker: matches('#login-modal,#loginModalContentContainer,[data-e2e="login-modal"],[data-e2e="login-container"]'),
        auth_form: Array.from(e.querySelectorAll('input[type="password"],form[action*="login"]')).some(visible)
          || (heading.some(t => /^(log in|sign in|đăng nhập)( to tiktok)?$/.test(t))
            && Array.from(e.querySelectorAll('input,button,[role="button"]')).some(visible)),
        challenge_marker: matches(challengeSelector),
        consent_marker: matches('[data-e2e="cookie-banner"],[data-e2e="consent-modal"]'),
        comment_panel: matches('[data-e2e="comment-list"],[data-e2e="comment-level-1"],[class*="DivCommentListContainer"]')
          || heading.some(t => /^(comments|bình luận)(\s*\(\d+\))?$/.test(t)),
        share_marker: matches('[data-e2e="share-dialog"],[data-e2e="share-modal"]')
          || heading.some(t => /^(share|share to|chia sẻ)$/.test(t))
      };
    });
  const text = (document.body?.innerText || '').toLowerCase();
  const challenge_details = challengeMarkers.map(selector => {
    const e = Array.from(document.querySelectorAll(selector)).find(visible);
    if (!e) return null;
    const r = e.getBoundingClientRect();
    return {
      selector, tag: e.tagName, visible: r.width > 0 && r.height > 0,
      bounding_rect: { width: Math.round(r.width), height: Math.round(r.height), top: Math.round(r.top), left: Math.round(r.left) },
      width: Math.round(r.width), height: Math.round(r.height),
      descendant_count: e.querySelectorAll('*').length
    };
  }).filter(Boolean);
  const challenge_text_signals = ['verify you are human', 'verifying your browser', 'security check',
    'security verification', 'complete the captcha', 'please wait', 'access denied'].filter(s => text.includes(s));
  const activeCaptcha = ['#captcha_container', '.captcha-verify-container', '[id*="captcha-verify"]'].some(anyVisible);
  const activeText = challenge_text_signals.some(s => s !== 'please wait' && s !== 'access denied');
  const activeDialog = dialogs.some(d => d.challenge_marker);
  const isInterstitial = challengeMarkers.includes('._wafchallengeid') && challenge_text_signals.includes('please wait')
    && !Array.from(document.querySelectorAll('[data-e2e="video-desc"],video,header,nav')).some(visible);
  const isChallenge = activeCaptcha || activeText || activeDialog || isInterstitial;
  const challengeReason = activeCaptcha ? 'ACTIVE_CAPTCHA_CONTAINER' : activeText ? 'CHALLENGE_TEXT_SIGNAL' : activeDialog ? 'CHALLENGE_DIALOG' : isInterstitial ? 'AUTOMATIC_INTERSTITIAL' : null;
  return {
    dialog_evidence: dialogs, challenge: isChallenge, challenge_markers: challengeMarkers,
    challenge_details, challenge_text_signals, challenge_reason: challengeReason,
    access_denied: /^http error 403\b/.test(text.trim()) || location.href.startsWith('chrome-error://'),
    search: Array.from(document.querySelectorAll('a[href*="/video/"]')).some(visible),
    homepage: anyVisible('header,nav,[data-e2e="nav-foryou"],[data-e2e="tiktok-logo"],a[href="/foryou"],a[href="/search"]'),
    video_detail: anyVisible('[data-e2e="video-desc"],video'),
    comments: anyVisible('[data-e2e="comment-level-1"]'), ready_state: document.readyState,
    comment_card_count: Array.from(document.querySelectorAll('[data-e2e="comment-level-1"]')).filter(visible).length,
    comment_control_present: anyVisible('[data-e2e="comment-icon"]'),
    empty_state: text.includes('no results found') || text.includes('video unavailable') || text.includes('video has been removed'),
    playback_error: text.includes('trouble playing this video')
  };
}
