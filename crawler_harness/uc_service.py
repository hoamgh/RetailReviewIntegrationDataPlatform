"""Contained-worker-only Selenium Service creation hook; no replacement policy.

No installed library is edited. The temporary class binding covers startup only.
Every later start/reconnect attempt is refused, including a swallowed UC error.
"""
from contextlib import contextmanager
from dataclasses import asdict
from unittest.mock import patch
from urllib.parse import urlparse

from .containment import ContainmentError


def owns_listener(identity, port):
    import psutil
    return any(c.status==psutil.CONN_LISTEN and c.laddr.port==port and
               c.laddr.ip in {'127.0.0.1','::1'}
               for c in psutil.Process(identity.pid).net_connections(kind='tcp'))


def service_class(base, api, listener=owns_listener):
    created=[]
    class OwnedService(base):
        _managed_process=None
        _managed_identity=None
        _managed_open=True
        _managed_violation=False
        _managed_reuses=0

        def _start_process(self, path):
            if created or self._managed_process is not None:
                self._managed_violation=True
                raise ContainmentError('Service replacement refused')
            created.append(self)
            super()._start_process(path)
            # Popen retains the original Windows creation handle. Never select
            # a same-name/PID process or recover a different process from a port.
            self._managed_process=self.process
            handle=int(self.process._handle)
            self._managed_identity=api.identity(handle)
            api.member(handle,None)
            if api.wait(handle):raise ContainmentError('New service already stopped')

        def validate_owned(self):
            process=self._managed_process
            if self._managed_violation or process is None or self.process is not process:
                raise ContainmentError('Unapproved service lifecycle transition')
            handle=int(process._handle)
            if api.identity(handle)!=self._managed_identity or api.wait(handle):
                raise ContainmentError('Original service creation handle stopped/changed')
            api.member(handle,None)
            if not listener(self._managed_identity,self.port):
                raise ContainmentError('Driver endpoint not owned by retained service')
            if api.identity(handle)!=self._managed_identity or api.wait(handle):
                raise ContainmentError('Service changed during endpoint observation')

        def start(self):
            if not self._managed_open:
                self._managed_violation=True
                raise ContainmentError('Post-startup service restart refused')
            if self._managed_process is not None:
                self.validate_owned()
                self._managed_reuses+=1
                return  # Startup connect(): keep the proven existing service.
            super().start()
            self.validate_owned()

        def seal(self):
            self.validate_owned()
            self._managed_open=False

        def evidence(self):
            return {'creation':asdict(self._managed_identity),'creations':1,
                    'startup_reuses':self._managed_reuses,'sealed':not self._managed_open}

    return OwnedService


@contextmanager
def startup_service(api):
    from importlib.metadata import version
    if version('seleniumbase')!='4.54.11' or version('selenium')!='4.49.0':
        raise ContainmentError('Unaudited UC/Selenium service lifecycle version')
    import selenium.webdriver.chrome.service as module
    owned=service_class(module.Service,api)
    # UC resolves this exact module attribute at construction. Worker-exclusive
    # scope; no global parent patch and no library files/config are modified.
    with patch.object(module,'Service',owned):
        yield owned


def endpoint_matches(driver, service):
    endpoint=urlparse(driver.command_executor._client_config.remote_server_addr)
    target=urlparse(service.service_url)
    return (endpoint.scheme==target.scheme=='http' and endpoint.port==target.port==service.port
            and endpoint.hostname in {'localhost','127.0.0.1','::1'}
            and target.hostname in {'localhost','127.0.0.1','::1'}
            and not endpoint.username and not endpoint.password
            and endpoint.path in {'','/'})
