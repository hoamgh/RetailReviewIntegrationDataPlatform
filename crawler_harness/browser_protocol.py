"""Bounded JSON IPC protocol; no pickle, raw driver or remote code transport."""
import ctypes as c
from ctypes import wintypes as w
import json

from .containment import ContainmentError

MAX_MESSAGE=8*1024*1024
DRIVER_READ={'current_url','window_handles','current_window_handle'}
DRIVER_CALL={'get','execute_script','find_element','find_elements','save_screenshot',
             'get_log','execute_cdp_cmd','set_page_load_timeout','set_script_timeout'}
ELEMENT_READ={'text','tag_name','size'}
ELEMENT_CALL={'click','is_displayed','find_element','find_elements','get_attribute'}
CDP={'Network.enable','Network.getResponseBody','Page.stopLoading'}


def send(conn,value):
    data=json.dumps(value,allow_nan=False,separators=(',',':')).encode()
    if len(data)>MAX_MESSAGE:raise ContainmentError('IPC message exceeds bound')
    conn.send_bytes(data)


def receive(conn):
    return json.loads(conn.recv_bytes(MAX_MESSAGE))


def peer_pid(conn,*,server):
    k=c.WinDLL('kernel32',use_last_error=True)
    f=getattr(k,'GetNamedPipeServerProcessId' if server else 'GetNamedPipeClientProcessId')
    f.argtypes=[w.HANDLE,c.POINTER(w.DWORD)];f.restype=w.BOOL
    pid=w.DWORD()
    if not f(conn.fileno(),c.byref(pid)):raise ContainmentError('IPC peer ownership unavailable')
    return pid.value
