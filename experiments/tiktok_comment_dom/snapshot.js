(replyTarget = null) => {
  const visible = e => {
    if (!e || !e.getClientRects().length) return false;
    const s = typeof getComputedStyle === 'function' ? getComputedStyle(e) : {};
    if (s.visibility === 'hidden' || s.display === 'none') return false;
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const overflow = e => typeof getComputedStyle === 'function' ? getComputedStyle(e).overflowY : '';
  const attributes = e => e ? Object.fromEntries(Array.from(e.attributes)
    .filter(a => /^(data-(comment-id|cid|user-id|uid|author-id|e2e)|id|role|aria-label|datetime)$/.test(a.name))
    .map(a => [a.name, a.value])) : {};
  const selector = '[data-e2e="comment-level-1"]';
  const readCard = (text, reply = false) => {
      const level = reply ? '2' : '1';
      const ownSelector = '[data-e2e="comment-level-' + level + '"]';
      let item = text.closest('[class*="DivCommentItemWrapper"],[data-e2e="comment-item"]');
      if (reply && item?.querySelector(selector)) item = null; // Never inherit the parent's source ID.
      // New layouts may omit the historical wrapper class; use the nearest single-comment ancestor.
      if (!item) {
        let node = text.parentElement;
        for (let i = 0; i < 6 && node && node !== document.body; i++, node = node.parentElement) {
          if (reply && node.querySelector(selector)) break;
          if (node.querySelectorAll(ownSelector).length > 1) break;
          if (node.querySelector('[data-e2e="comment-username-' + level + '"],a[href*="/@"]')) item = node;
        }
      }
      const root = item?.closest('[class*="DivCommentObjectWrapper"]') || item;
      if (!item) return { text: text.innerText, unsupported_card_shape: true, raw_attributes: attributes(text) };
      const name = item.querySelector('[data-e2e="comment-username-' + level + '"]');
      const links = Array.from(item.querySelectorAll('a[href*="/@"]'));
      const author = links.find(a => (a.innerText || '').trim()) || links[0];
      const date = item.querySelector('[data-e2e="comment-time"],[data-e2e="comment-time-1"],[data-e2e="comment-date"],time')
        || item.querySelector('[class*="DivCommentSubContentWrapper"] span');
      const like = item.querySelector('[data-e2e="comment-like-count"]')
        || item.querySelector('[data-key-interaction="comment_like"],[data-e2e="comment-like-icon"],[class*="DivLikeContainer"]');
      const replies = Array.from(root.querySelectorAll('button,[role="button"],p')).find(e => visible(e) &&
        /^(?:View|See)\s+\d+\s+(?:repl(?:y|ies)|more)\b|^Xem\s+(?:thêm\s+)?\d+\s+(?:phản hồi|câu trả lời)/i.test((e.innerText || '').trim()));
      return {
        text: text.innerText || '', author_display_name: name?.innerText || author?.innerText || null,
        author_href_raw: author?.getAttribute('href') || null,
        author_attributes: attributes(author), item_attributes: attributes(item), root_attributes: reply ? {} : attributes(root),
        created_at_raw: date?.innerText || null, date_attributes: attributes(date),
        like_count_raw: like?.innerText || null, like_aria_label: like?.getAttribute('aria-label') || null,
        reply_count_raw: replies?.innerText || null, unsupported_card_shape: false
      };
    };
  const cards = Array.from(document.querySelectorAll(selector)).filter(e => visible(e)
    && !e.closest('[data-e2e="comment-level-2"]')).slice(0, 50).map(e => readCard(e));
  const replies = Array.from(document.querySelectorAll('[data-e2e="comment-level-2"]')).filter(visible).slice(0, 50).map(e => {
    const row = readCard(e, true);
    row.record_type = 'REPLY';
    // Find the nearest structural parent even without the old thread class.
    // Never assign a parent from a shared list containing multiple top-level cards.
    for (let node = e.parentElement, depth = 0;
         node && node !== document.body && node !== document.documentElement && depth < 16;
         node = node.parentElement, depth++) {
      const parents = Array.from(node.querySelectorAll(selector));
      if (parents.length > 1 || node.querySelector('video,[id^="media-card-"]')) break;
      if (parents.length === 1) { row.parent_card = readCard(parents[0]); break; }
    }
    return row;
  });
  const expansionTargets = [];
  for (const text of Array.from(document.querySelectorAll(selector)).filter(visible)) {
    // Modern cards can omit the historical thread wrapper. Stop before a
    // shared list ancestor: a control must belong to exactly one parent.
    let root = null;
    for (let node = text.parentElement, depth = 0;
         node && node !== document.body && node !== document.documentElement && depth < 16;
         node = node.parentElement, depth++) {
      if (node.querySelectorAll(selector).length !== 1) break;
      if (node.querySelector('video,[id^="media-card-"]')) break;
      const hasControl = Array.from(node.querySelectorAll('button,[role="button"],p')).some(e => visible(e) &&
        /^(?:View|See)\s+\d+\s+(?:repl(?:y|ies)|more)\b|^Xem\s+(?:thêm\s+)?\d+\s+(?:phản hồi|câu trả lời)/i.test((e.innerText || '').trim()));
      if (hasControl) { root = node; break; }
    }
    if (!root) continue;
    const controls = new Set(Array.from(root.querySelectorAll('button,[role="button"],p')).filter(e => visible(e) &&
      /^(?:View|See)\s+\d+\s+(?:repl(?:y|ies)|more)\b|^Xem\s+(?:thêm\s+)?\d+\s+(?:phản hồi|câu trả lời)/i.test((e.innerText || '').trim()))
      .map(e => e.closest('button,[role="button"]') || e));
    for (const element of controls) expansionTargets.push({element, parent_card: readCard(text), label: element.innerText.trim()});
  }
  const parentSignature = c => [c.text, c.author_href_raw, c.created_at_raw,
    c.item_attributes?.['data-comment-id'], c.item_attributes?.['data-cid'],
    c.root_attributes?.['data-comment-id'], c.root_attributes?.['data-cid']].map(x => x || null);
  if (replyTarget) {
    const matches = expansionTargets.filter(t => t.label === replyTarget.label &&
      JSON.stringify(parentSignature(t.parent_card)) === JSON.stringify(parentSignature(replyTarget.parent_card)));
    return matches.length === 1 ? matches[0].element : null; // Never click an ambiguous/stale index.
  }
  const first = document.querySelector(selector);
  let container = first?.closest('[class*="DivCommentListContainer"],[data-e2e="comment-list"]');
  const scroll = (__COMMENT_CONTAINER__)();
  const safeScroll = !!scroll;
  const scope = container || scroll;
  // Only explicit list-scoped UI markers qualify; bottom/no-growth never does.
  const endMarker = scope?.querySelector('[data-e2e="comment-list-end"]');
  const emptyMarker = scope?.querySelector('[data-e2e="comment-list-empty"]');
  const endObserved = !!(endMarker && visible(endMarker) &&
    /^(no more comments|you.ve reached the end|không còn bình luận)$/i.test((endMarker.innerText || '').trim()));
  const emptyObserved = !!(emptyMarker && visible(emptyMarker) &&
    /^(no comments yet|chưa có bình luận)$/i.test((emptyMarker.innerText || '').trim()));
  const countHeading = Array.from(document.querySelectorAll('h1,h2,h3,h4,div,span')).find(e => visible(e)
    && /^(?:\d+\s+comments|comments\s*\(\d+\)|bình luận\s*\(\d+\))$/i.test((e.innerText || '').trim()) && e.querySelectorAll(selector).length === 0);
  const heading = countHeading?.innerText || null;
  const loadingScope = container || scroll || countHeading?.parentElement;
  const commentsLoading = !!loadingScope && Array.from(loadingScope.querySelectorAll(
    '[aria-busy="true"],[data-e2e="comment-loading"],[class*="Skeleton"],[class*="skeleton"]'
  )).some(visible);
  const control = Array.from(document.querySelectorAll('[data-e2e="comment-icon"]')).filter(visible)
    .map(e => ({ attributes: attributes(e), text: e.innerText }));
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"]')).filter(visible)
    .map(e => (e.innerText || '').slice(0, 1000));
  const activeCaptcha = Array.from(document.querySelectorAll('#captcha_container,.captcha-verify-container,[id*="captcha-verify"]'))
    .find(e => visible(e));
  const textSnippet = (document.body?.innerText || '').slice(0, 2000);
  const textLower = textSnippet.toLowerCase();
  const challengeSignals = ['verify you are human', 'verifying your browser', 'security check',
    'security verification', 'complete the captcha'].some(s => textLower.includes(s));
  return {
    url: document.location.href, title: document.title, ready_state: document.readyState,
    body_text_snippet: textSnippet, controls: control, dialogs,
    challenge_present: !!activeCaptcha || challengeSignals,
    comment_panel_present: !!container || !!countHeading || cards.length > 0,
    comments_loading: commentsLoading, comment_heading: heading, cards, replies,
    reply_targets: expansionTargets.map(({parent_card,label}) => ({parent_card,label})),
    reply_controls_observed: scope ? new Set(Array.from(scope.querySelectorAll('button,[role="button"],p')).filter(e => visible(e) &&
      /^(?:View|See)\s+\d+\s+(?:repl(?:y|ies)|more)\b|^Xem\s+(?:thêm\s+)?\d+\s+(?:phản hồi|câu trả lời)/i.test((e.innerText || '').trim()))
      .map(e => e.closest('button,[role="button"]') || e)).size : null,
    end_of_comments_observed: endObserved, empty_comments_observed: emptyObserved,
    scroll_container: safeScroll ? {
      class: scroll.className, role: scroll.getAttribute('role'),
      scroll_top: scroll.scrollTop, client_height: scroll.clientHeight, scroll_height: scroll.scrollHeight,
      at_bottom: scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 2,
      selection: 'VISIBLE_COMMENT_ANCESTOR'
    } : null
  };
}
