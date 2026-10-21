() => {
  // Start at the comment itself: modern layouts need not have historical classes.
  const visible = e => e && e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const comments = Array.from(document.querySelectorAll('[data-e2e="comment-level-1"]')).filter(visible);
  for (const comment of comments) {
    const item = comment.closest('[class*="DivCommentItemWrapper"],[data-e2e="comment-item"]');
    let e = comment.parentElement;
    for (let depth = 0; depth < 24 && e && e !== document.body && e !== document.documentElement;
         depth++, e = e.parentElement) {
      if (visible(e) && e.clientHeight > 0 && e.scrollHeight > e.clientHeight + 2 &&
          /^(auto|scroll|overlay)$/.test(getComputedStyle(e).overflowY) &&
          (!item || (e !== item && e.contains(item))) &&
          !e.querySelector('[id^="media-card-"],video') && e.contains(comment)) return e;
    }
  }
  return null;
}
