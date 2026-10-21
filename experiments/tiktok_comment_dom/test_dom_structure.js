// Executes shipped JS against a minimal synthetic DOM, never a browser/network.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
class Element {
  constructor(tag, attrs = {}, text = '', parent = null) {
    this.tag = tag; this.attrs = attrs; this.text = text; this.children = [];
    this.parentElement = parent; if (parent) parent.children.push(this);
    this.scrollTop = 0; this.scrollHeight = 100; this.clientHeight = 100;
    this.style = {visibility: 'visible', display: 'block', overflowY: 'visible'};
  }
  get innerText() { return [this.text,...this.children.map(e=>e.innerText)].filter(Boolean).join(' '); }
  get className() { return this.attrs.class || ''; }
  get attributes() { return Object.entries(this.attrs).map(([name,value])=>({name,value})); }
  getAttribute(k) { return this.attrs[k] || null; }
  getClientRects() { return this.style.display === 'none' ? [] : [{}]; }
  getBoundingClientRect() { return {width:100,height:50}; }
  matches(selector) {
    return selector.split(',').some(s=>{
      s=s.trim();
      const tag=s.match(/^[a-z][\w-]*/i); if(tag && this.tag!==tag[0]) return false;
      const id=s.match(/^#([\w-]+)/); if(id && this.attrs.id!==id[1]) return false;
      const cls=s.match(/^\.([\w-]+)/); if(cls && !(this.attrs.class||'').split(' ').includes(cls[1])) return false;
      for(const m of s.matchAll(/\[([^=\]*^]+)(\*=|\^=|=)?(?:"([^"]*)"|'([^']*)')?\]/g)) {
        const val=this.attrs[m[1]], expect=m[3] || m[4] || '';
        if(val===undefined || (m[2]==='=' && val!==expect) ||
          (m[2]==='*=' && !val.includes(expect)) || (m[2]==='^=' && !val.startsWith(expect))) return false;
      }
      return !!(tag||id||cls||s.startsWith('['));
    });
  }
  querySelectorAll(s) { return this.children.flatMap(e=>[...(e.matches(s)?[e]:[]),...e.querySelectorAll(s)]); }
  querySelector(s) { return this.querySelectorAll(s)[0] || null; }
  closest(s) { for(let e=this;e;e=e.parentElement) if(e.matches(s)) return e; return null; }
  contains(e) { for(;e;e=e.parentElement) if(e===this) return true; return false; }
}
const html=new Element('html'), body=new Element('body',{},'',html);
const pane=new Element('section',{},'',body);pane.style.overflowY='auto';pane.clientHeight=100;pane.scrollHeight=500;
const thread=new Element('div',{'data-e2e':'comment-thread',class:'DivCommentObjectWrapper'},'',pane);
const top=new Element('div',{'data-e2e':'comment-item','data-comment-id':'parent-a'},'',thread);
new Element('a',{'data-e2e':'comment-username-1',href:'/@fixture'},'Fixture author',top);
const text=new Element('p',{'data-e2e':'comment-level-1'},'Synthetic parent',top);
const reply=new Element('div',{'data-e2e':'comment-item','data-comment-id':'reply-a'},'',thread);
new Element('a',{'data-e2e':'comment-username-2',href:'/@fixture_reply'},'Fixture reply author',reply);
new Element('p',{'data-e2e':'comment-level-2'},'Synthetic reply',reply);
const button=new Element('button',{},'',thread);new Element('p',{},'View 3 replies',button);
global.getComputedStyle=e=>e.style;
global.document={body,documentElement:html,location:{href:'https://www.tiktok.com/@fixture/video/123'},
  title:'Synthetic fixture',readyState:'complete',querySelectorAll:s=>html.querySelectorAll(s),querySelector:s=>html.querySelector(s)};
const helper=fs.readFileSync(path.join(__dirname,'scroll_container.js'),'utf8');
const find=new Function('return ('+helper+')')();
assert.equal(find(),pane); // No historical list class is present.
top.style.overflowY='auto';top.scrollHeight=300;
assert.equal(find(),pane); // A long individual comment is not the list scroll surface.
top.style.overflowY='visible';
const source=fs.readFileSync(path.join(__dirname,'snapshot.js'),'utf8').replace('__COMMENT_CONTAINER__',helper);
const read=new Function('return ('+source+')')();
let s=read();assert.equal(s.cards.length,1);assert.equal(s.replies.length,1);
// A visible comment skeleton means the panel is still loading. Hidden or
// unrelated page loaders must not block the comment collector.
const skeleton=new Element('div',{class:'CommentSkeleton'},'',pane);
assert.equal(read().comments_loading,true);
skeleton.style.display='none';assert.equal(read().comments_loading,false);
new Element('div',{'aria-busy':'true'},'',body);
assert.equal(read().comments_loading,false);
assert.equal(s.reply_targets.length,1);
assert.equal(read(s.reply_targets[0]),button);
assert.equal(read({...s.reply_targets[0],label:'Hide replies'}),null);
assert.equal(s.replies[0].item_attributes['data-comment-id'],'reply-a');
assert.deepEqual(s.replies[0].root_attributes,{});
assert.equal(s.replies[0].parent_card.item_attributes['data-comment-id'],'parent-a');
assert.equal(s.reply_controls_observed,1); // Nested p/button is one control.
assert.equal(s.scroll_container.scroll_height,500);assert.equal(s.end_of_comments_observed,false);
pane.scrollTop=400;s=read();assert.equal(s.scroll_container.at_bottom,true);assert.equal(s.end_of_comments_observed,false);
const end=new Element('p',{'data-e2e':'comment-list-end'},'No more comments',pane);
assert.equal(read().end_of_comments_observed,true);end.style.display='none';assert.equal(read().end_of_comments_observed,false);
// A feed/video ancestor and the page body cannot be selected.
const video=new Element('video',{},'',pane);assert.equal(find(),null);
pane.children=pane.children.filter(e=>e!==video);pane.style.overflowY='hidden';assert.equal(find(),null);
pane.style.overflowY='auto';pane.style.display='none';assert.equal(find(),null);
pane.style.display='block';text.style.visibility='hidden';assert.equal(find(),null);
// Missing historical wrapper, reply control outside the inner comment item.
text.style.visibility='visible';
delete thread.attrs.class; delete thread.attrs['data-e2e'];
const second=new Element('div',{'data-e2e':'comment-item'},'',pane);
new Element('a',{href:'/@second'},'Second fixture',second);
new Element('p',{'data-e2e':'comment-level-1'},'Other parent',second);
s=read();assert.equal(s.cards.length,2);assert.equal(s.reply_targets.length,1);
assert.equal(read(s.reply_targets[0]),button);
assert.equal(s.replies[0].parent_card.item_attributes['data-comment-id'],'parent-a');
const heading=new Element('h2',{},'Bình luận (11)',pane);
assert.equal(read().comment_heading,'Bình luận (11)');
heading.style.display='none';
// Observed UI sequence: one child already visible, remaining children behind "View 3 more".
button.children[0].text='View 3 more';
s=read();assert.equal(s.replies.length,1);assert.equal(s.reply_targets.length,1);
assert.equal(s.reply_targets[0].label,'View 3 more');
assert.equal(read(s.reply_targets[0]),button);
button.children[0].text='Hide';assert.equal(read().reply_targets.length,0);
button.children[0].text='View 3 more';
// A control on a shared list cannot be attributed to either parent.
thread.children=thread.children.filter(e=>e!==button);
button.parentElement=pane;pane.children.push(button);
assert.equal(read().reply_targets.length,0);
assert.equal(read(s.reply_targets[0]),null); // stale target fails closed
console.log('Synthetic DOM scroll/reply regressions passed');
