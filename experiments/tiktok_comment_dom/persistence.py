"""TikTok comment storage adapter: atomic collector state + durable Parquet outbox."""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
import os
from contextlib import contextmanager
from pathlib import Path

FIELDS = ('storage_key','schema_version','place_id','video_id','identity_kind','identity_scope',
          'observed_comment_id','canonical_comment_id','author_id','author_username','author_name',
          'text','timestamp_raw','timestamp_machine_raw','first_observed_at','observed_at',
          'like_count','reply_count','source','provenance','content_hash','revision','run_id')
INTS = {'schema_version','like_count','reply_count','revision'}


class SourceRunConflict(ValueError):
    """Managed refresh cannot overwrite a source-ID row from another data-run."""


def storage_identity(item, place_id, video_id, run_id):
    cid = item.get('comment_id_candidate')
    kind = 'DOM_ID_CANDIDATE' if cid else 'SESSION_FALLBACK'
    scope = 'video' if cid else run_id
    identity = cid or item['session_dedup_fingerprint']
    if item.get('is_reply'): identity = ['REPLY', identity]
    return digest([place_id, video_id, kind, scope, identity]), kind, scope, cid

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def outcome(checkpoint, evidence=None):
    evidence=evidence or {}
    # Only required endpoint denial is authoritative; ancillary errors stay telemetry.
    status=evidence.get('required_http_status')
    reason=checkpoint.get('pagination',{}).get('stop_reason')
    if status==429 or evidence.get('rate_limited') or reason=='RATE_LIMITED': return 'RATE_LIMITED'
    if status==403 or evidence.get('challenge') or reason in ('ACCESS_DENIED','CHALLENGE','LOGIN_OR_CONSENT_GATE','AUTH_GATE'): return 'ACCESS_DENIED'
    if evidence.get('failed') or reason in ('UNKNOWN_403','REQUIRED_ENDPOINT_ERROR','APPLICATION_ERROR'): return 'FAILED'
    if checkpoint.get('pagination',{}).get('exhausted') is True and not checkpoint.get('reply_limit', 0): return 'COMPLETE'
    return 'PARTIAL'

class CommentPersistence:
    """Single SQLite owner for TikTok comments. Never use the Google state database."""
    def __init__(self, database, parquet_root, *, commit_guard=None, commit_observer=None, publication_enabled=True):
        if commit_observer is not None and commit_guard is None:
            raise ValueError('Commit observer requires a transaction guard')
        self.commit_guard=commit_guard;self.commit_observer=commit_observer
        self.publication_enabled=publication_enabled
        self.path=Path(database);self.lake=Path(parquet_root)
        if 'google_maps_reviews' in self.lake.parts:raise ValueError('Google Parquet output is protected')
        if self.path.name=='crawler_state.sqlite3':raise ValueError('Google state database is protected')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path)
        self.db.row_factory=sqlite3.Row
        existing={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if existing.intersection({'review_state','pending_review_events'}):
            self.db.close();raise ValueError('Cannot use a Google review database')
        columns=','.join(f'{f} {"INTEGER" if f in INTS else "TEXT"}' for f in FIELDS)
        self.db.executescript(f'''CREATE TABLE IF NOT EXISTS tiktok_comments({columns},PRIMARY KEY(storage_key));
            CREATE TABLE IF NOT EXISTS tiktok_comment_events(event_id TEXT PRIMARY KEY,payload TEXT NOT NULL,delivered INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS tiktok_comment_checkpoints(run_id TEXT,place_id TEXT,video_id TEXT,input_digest TEXT,next_page INTEGER,state_json TEXT,status TEXT,PRIMARY KEY(run_id,place_id,video_id));''')
        actual=[r[1] for r in self.db.execute('PRAGMA table_info(tiktok_comments)')]
        if actual!=list(FIELDS):
            self.db.close();raise ValueError('Unsupported TikTok comment schema; no automatic migration')

    @staticmethod
    def schema():
        import pyarrow as pa
        return pa.schema([(f,pa.int64() if f in INTS else pa.string()) for f in FIELDS]+[('event_id',pa.string()),('change_type',pa.string())],metadata={b'tiktok_comment_schema_version':b'1'})

    def load_checkpoint(self,run_id,place_id,video_id):
        row=self.db.execute('SELECT * FROM tiktok_comment_checkpoints WHERE run_id=? AND place_id=? AND video_id=?',(run_id,place_id,video_id)).fetchone()
        return dict(row) if row else None

    def persist(self,session,*,run_id,place_id,input_digest,next_page,provenance,evidence=None,final_observation=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',run_id):raise ValueError('Invalid run_id')
        if not place_id:raise ValueError('Explicit place_id required')
        checkpoint=json.loads(json.dumps(session.checkpoint()))
        for row in checkpoint['records']:row.pop('raw_evidence',None)
        counts={'INSERT':0,'UPDATE':0,'UNCHANGED':0}
        context={'run_id':run_id,'place_id':place_id,'video_id':session.video_id,
                 'input_digest':input_digest,'checkpoint':checkpoint,'next_page':next_page,
                 'final_observation':final_observation,'committed_event_ids':[]}
        transaction=self.commit_guard(self,context) if self.commit_guard else self.db
        with transaction as authority:
            # Guard holds BEGIN IMMEDIATE: check the whole batch before any DML.
            if self.commit_guard:
                for item in session.records.values():
                    key,kind,_,_=storage_identity(item,place_id,session.video_id,run_id)
                    previous=self.db.execute('SELECT run_id FROM tiktok_comments WHERE storage_key=?',(key,)).fetchone()
                    if kind=='DOM_ID_CANDIDATE' and previous and previous['run_id']!=run_id:
                        raise SourceRunConflict('CROSS_RUN_SOURCE_ID_CONFLICT')
            for item in session.records.values():
                key,kind,scope,cid=storage_identity(item,place_id,session.video_id,run_id)
                item_provenance = dict(provenance)
                if session.reply_limit:
                    item_provenance['comment_relationship'] = {field:item.get(field) for field in
                        ('record_type','parent_comment_id','parent_session_key','parent_link_kind')}
                row=dict(storage_key=key,schema_version=1,place_id=place_id,video_id=session.video_id,
                    identity_kind=kind,identity_scope=scope,observed_comment_id=cid,canonical_comment_id=None,
                    author_id=item.get('author_id_candidate'),author_username=item.get('author_username'),
                    author_name=item.get('author_display_name'),text=item.get('text'),
                    timestamp_raw=item.get('created_at_raw'),timestamp_machine_raw=item.get('created_at_machine_raw'),
                    first_observed_at=item.get('first_observed_at',item['observed_at']),observed_at=item['observed_at'],
                    like_count=item.get('like_count'),reply_count=item.get('reply_count'),source='DOM',
                    provenance=json.dumps(item_provenance,ensure_ascii=False,sort_keys=True),run_id=run_id)
                previous=self.db.execute('SELECT * FROM tiktok_comments WHERE storage_key=?',(key,)).fetchone()
                if previous:
                    row['first_observed_at']=previous['first_observed_at']
                    for field in ('timestamp_raw','timestamp_machine_raw','author_id','author_username','author_name','like_count','reply_count'):
                        if row[field] is None:row[field]=previous[field]
                content={f:row[f] for f in ('text','author_id','author_username','author_name','timestamp_raw','timestamp_machine_raw','like_count','reply_count')}
                if session.reply_limit: content['comment_relationship']=item_provenance['comment_relationship']
                row['content_hash']=digest(content)
                change='INSERT' if not previous else 'UNCHANGED' if previous['content_hash']==row['content_hash'] else 'UPDATE'
                row['revision']=(previous['revision'] if previous else 0)+(change!='UNCHANGED')
                counts[change]+=1
                self.db.execute(f'INSERT OR REPLACE INTO tiktok_comments({",".join(FIELDS)}) VALUES({",".join("?" for _ in FIELDS)})',[row[f] for f in FIELDS])
                if change!='UNCHANGED':
                    event=digest([key,row['revision']]);payload={**row,'event_id':event,'change_type':change}
                    self.db.execute('INSERT INTO tiktok_comment_events(event_id,payload) VALUES(?,?)',(event,json.dumps(payload,ensure_ascii=False)))
                    context['committed_event_ids'].append(event)
            status=outcome(checkpoint,evidence)
            self.db.execute('INSERT OR REPLACE INTO tiktok_comment_checkpoints VALUES(?,?,?,?,?,?,?)',
                (run_id,place_id,session.video_id,input_digest,next_page,json.dumps(checkpoint,ensure_ascii=False),status))
            if self.commit_observer:
                self.commit_observer(self,context,authority)
        return {**counts,'status':status}

    @contextmanager
    def _legacy_publication_transaction(self):
        if self.db.in_transaction:
            raise ValueError('Publication requires its own transaction')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            has_ledger=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='harness_events'").fetchone()
            if has_ledger and self.db.execute("SELECT 1 FROM harness_events WHERE event_type IN ('source.event_committed','job.publication_sealed') LIMIT 1").fetchone():
                raise RuntimeError('Managed database requires guarded scoped publication')
            yield None
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def flush(self, *, event_ids=None, publication_guard=None, fault=None):
        if not self.publication_enabled:
            raise RuntimeError('Managed publication deferred to M3-C')
        import pyarrow as pa
        import pyarrow.parquet as pq
        if event_ids is not None and publication_guard is None:
            raise ValueError('Scoped publication requires a transaction guard')
        if publication_guard is not None and event_ids is None:
            raise ValueError('Managed publication requires exact event membership')
        count=0
        ids = ([r[0] for r in self.db.execute('SELECT event_id FROM tiktok_comment_events WHERE delivered=0 ORDER BY rowid')]
               if event_ids is None else sorted(set(event_ids)))
        for eid in ids:
            transaction = publication_guard(self,eid) if publication_guard else self._legacy_publication_transaction()
            with transaction as revalidate:
                # Re-read under the managed source transaction; do not use a
                # stale pre-lock payload or ack another event selected globally.
                event=self.db.execute('SELECT * FROM tiktok_comment_events WHERE event_id=?',(eid,)).fetchone()
                if event is None:raise RuntimeError('Selected outbox event missing')
                if event['delivered']:continue
                row=json.loads(event['payload']);date=row['observed_at'][:10]
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date):raise ValueError('Observed date required for partition')
                target=self.lake/f'crawl_date={date}'/f'run_id={row["run_id"]}'/f'event-{eid}.parquet'
                target.parent.mkdir(parents=True,exist_ok=True)
                if fault:fault('before_publish')
                if publication_guard:revalidate()
                if target.exists():
                    actual=pq.read_table(target,partitioning=None)
                    if actual.to_pylist()!=[row] or (publication_guard and not actual.schema.equals(self.schema(),check_metadata=True)):
                        raise RuntimeError('Existing Parquet event content mismatch')
                else:
                    temporary=target.with_suffix('.tmp')
                    pq.write_table(pa.Table.from_pylist([row],schema=self.schema()),temporary,compression='snappy')
                    if publication_guard:
                        with temporary.open('rb+') as handle:os.fsync(handle.fileno())
                        revalidate()
                    temporary.replace(target)
                    if fault:fault('after_rename')
                if fault:fault('before_ack')
                if publication_guard:revalidate()
                # Managed guard holds BEGIN IMMEDIATE across publication + ACK,
                # and revalidates the original lease deadline before commit.
                if publication_guard:
                    actual=pq.read_table(target,partitioning=None)
                    if not actual.schema.equals(self.schema(),check_metadata=True) or actual.to_pylist()!=[row]:
                        raise RuntimeError('Published Parquet verification failed')
                self.db.execute('UPDATE tiktok_comment_events SET delivered=1 WHERE event_id=? AND delivered=0',(eid,))
            count+=1
            if fault:fault('after_ack')
        return count

    def summary(self):
        return {'records':self.db.execute('SELECT COUNT(*) FROM tiktok_comments').fetchone()[0],
                'events':self.db.execute('SELECT COUNT(*) FROM tiktok_comment_events').fetchone()[0],
                'pending_events':self.db.execute('SELECT COUNT(*) FROM tiktok_comment_events WHERE delivered=0').fetchone()[0]}

    def close(self):self.db.close()

def replay_comments(data,*,storage,database,parquet_root,run_id,place_id,resume=False,max_pages=None):
    """Offline integration used by existing place-intelligence CLI and Storage."""
    from experiments.tiktok_comment_dom.extractor import CommentSession
    from experiments.tiktok_access_benchmark.crawler import output_record
    repository=CommentPersistence(database,parquet_root)
    storage.comment_repository=repository
    input_hash=digest(data);statuses=[];processed=0
    try:
        repository.flush()  # Recover delivery after a committed partial batch.
        for video in data['videos']:
            vid=str(video['video_id']);saved=repository.load_checkpoint(run_id,place_id,vid)
            if saved and saved['input_digest']!=input_hash:raise ValueError('Run input changed; use a new run_id')
            session=CommentSession.from_checkpoint(json.loads(saved['state_json'])) if resume and saved else CommentSession(vid,video.get('limit',50),video.get('reply_limit',0))
            start=saved['next_page'] if resume and saved else 0
            if resume and saved and saved['status'] in ('ACCESS_DENIED','RATE_LIMITED','FAILED'):
                storage.add([output_record(row,video,storage.summary['query']) for row in session.records.values()])
                statuses.append(saved['status']);break
            storage.add([output_record(row,video,storage.summary['query']) for row in session.records.values()])
            entries=data['snapshots_by_video'].get(vid,[])
            status=saved['status'] if resume and saved else 'PARTIAL'
            for index,entry in enumerate(entries[start:],start):
                if max_pages is not None and processed>=max_pages:break
                session.add(entry['snapshot'],entry['observed_at'])
                evidence=entry.get('evidence',{})
                result=storage.persist_collector(session,run_id=run_id,place_id=place_id,input_digest=input_hash,
                    next_page=index+1,provenance={'mode':'OFFLINE_DOM_REPLAY','input_digest':input_hash,'place_association':'EXPLICIT_CALLER_MAPPING','source_artifact':entry.get('source_artifact','saved_dom_projection')},evidence=evidence)
                storage.add([output_record(row,video,storage.summary['query']) for row in session.records.values()])
                status=result['status'];processed+=1
                repository.flush()
                if status in ('ACCESS_DENIED','RATE_LIMITED','FAILED','COMPLETE'):break
            statuses.append(status)
            if status in ('ACCESS_DENIED','RATE_LIMITED','FAILED') or max_pages is not None and processed>=max_pages:break
        overall=next((s for s in ('ACCESS_DENIED','RATE_LIMITED','FAILED') if s in statuses),
                     'COMPLETE' if len(statuses)==len(data['videos']) and statuses and all(s=='COMPLETE' for s in statuses) else 'PARTIAL')
        summary={**repository.summary(),'status':overall,'run_id':run_id,'place_id':place_id,'processed_pages':processed,
                 'database':str(Path(database).resolve()),'parquet_root':str(Path(parquet_root).resolve()),'live_validated':False}
        storage.summary.update(status=overall,persistence=summary)
        from types import SimpleNamespace
        storage.checkpoint(SimpleNamespace(request_count=0,entries=[]))
        return summary
    except BaseException as exc:
        storage.summary.update(status='PARTIAL' if isinstance(exc,KeyboardInterrupt) else 'FAILED',persistence_error_type=type(exc).__name__)
        from types import SimpleNamespace
        storage.checkpoint(SimpleNamespace(request_count=0,entries=[]))
        raise
    finally:storage.comment_repository=None;repository.close()
