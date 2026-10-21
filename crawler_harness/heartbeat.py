"""Lease renewal on a thread-owned SQLite connection; no process cleanup thread."""
import math
import threading

from .state import JobStore,StateError


class LeaseHeartbeat:
    def __init__(self,store,token,*,interval=1,lease_seconds=30):
        if (type(interval) not in (int,float) or not math.isfinite(interval) or interval<=0 or
                type(lease_seconds) not in (int,float) or not math.isfinite(lease_seconds) or
                lease_seconds<=interval*2):raise StateError('Bounded heartbeat interval/lease required')
        self.path,self.clock,self.token=store.path,store.clock,token
        self.interval,self.lease_seconds=interval,lease_seconds
        self.stop_event=threading.Event();self.lost=threading.Event();self.ready=threading.Event()
        self.thread=None;self.connection_thread=None;self.renewals=0

    def start(self):
        if self.thread is not None:raise StateError('Heartbeat cannot restart')
        def run():
            store=None
            try:
                store=JobStore(self.path,clock=self.clock)
                self.connection_thread=threading.get_ident()
                self.ready.set()
                while not self.stop_event.wait(self.interval):
                    store.heartbeat(self.token,lease_seconds=self.lease_seconds)
                    self.renewals+=1
            except Exception:self.lost.set();self.ready.set()
            finally:
                if store:store.close()
        self.thread=threading.Thread(target=run,name='harness-lease-heartbeat',daemon=True)
        self.thread.start()
        if not self.ready.wait(5) or self.lost.is_set():raise StateError('Heartbeat initialization failed')

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(12)
            if self.thread.is_alive():raise StateError('Heartbeat join unverified')
