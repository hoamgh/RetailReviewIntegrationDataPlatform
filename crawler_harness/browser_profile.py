"""Dedicated profile metadata and exclusive controller ownership, no adoption."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import uuid

from .containment import ContainmentError
from .publication import resource_id, windows_mutex


def atomic_json(path,data):
    path=Path(path)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('x',encoding='utf-8') as f:
        json.dump(data,f,allow_nan=False,sort_keys=True);f.flush();os.fsync(f.fileno())
    temporary.replace(path)


class DedicatedProfile:
    @staticmethod
    def create(path,database):
        path=Path(path).resolve()
        # Never stamp an existing user/legacy folder as harness owned.
        path.mkdir(parents=True,exist_ok=False)
        atomic_json(path/'harness-profile.json',{'version':1,'profile_id':uuid.uuid4().hex,
            'resource':resource_id(path,'profile'),'database':resource_id(database,'database'),
            'active_session':None})
        return path

    def __init__(self,path,owner):
        self.path=Path(path).resolve();self.owner=owner
        self.file=self.path/'harness-profile.json';self.lock=None;self.manifest=None

    def _read(self):
        try:value=json.loads(self.file.read_text(encoding='utf-8'))
        except (OSError,ValueError):raise ContainmentError('Not a verified dedicated profile') from None
        if (value.get('version')!=1 or value.get('resource')!=resource_id(self.path,'profile') or
                value.get('database')!=self.owner.database_id):
            raise ContainmentError('Profile resource/database mismatch')
        return value

    def __enter__(self):
        self.owner._owner()
        self.lock=windows_mutex('Global\\IntegrationRecommendationProfileV1_'+resource_id(self.path,'profile'))
        self.lock.__enter__()
        try:
            self.manifest=self._read()
            if self.manifest['active_session'] is not None:
                raise ContainmentError('Unresolved profile session; human review required')
            self.manifest['active_session']=self.owner.session_id
            atomic_json(self.file,self.manifest)
            return self
        except BaseException:
            self.lock.__exit__(None,None,None);self.lock=None
            raise

    def validate(self,profile_id,session_id):
        self.owner._owner(session_id,self.owner.token.attempt_id)
        current=self._read()
        if (self.lock is None or current!=self.manifest or current['profile_id']!=profile_id or
                current['active_session']!=session_id):
            raise ContainmentError('Profile/session ownership mismatch')

    def release_after_quiescence(self):
        self.validate(self.manifest['profile_id'],self.owner.session_id)
        # Only the retained C2a owner can produce this observation. No caller flag.
        if self.owner._created:self.owner.quiescence()
        elif self.owner.api.active(self.owner.job)!=0:raise ContainmentError('Profile tree still active')
        self.manifest['active_session']=None;atomic_json(self.file,self.manifest)

    def __exit__(self,*_):
        # Do NOT clear active metadata here: exceptions/lost ownership stay dirty.
        if self.lock:self.lock.__exit__(None,None,None);self.lock=None
