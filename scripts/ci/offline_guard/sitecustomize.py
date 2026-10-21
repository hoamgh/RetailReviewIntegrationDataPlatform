"""Cooperative test-only guard, not an OS firewall or hostile-code boundary.

The sole INET exception is CPython's Windows asyncio self-pipe socketpair.
No environment switch, arbitrary loopback listener, DNS or external connect.
"""
import asyncio.proactor_events as _proactor
import socket as _socket
import sys
import threading as _threading
import weakref as _weakref

_pair = _socket.socketpair
_pair_code = getattr(_pair, "__code__", None) if _pair.__name__ == "_fallback_socketpair" else None
_pipe_code = _proactor.BaseProactorEventLoop._make_self_pipe.__code__
_listeners = _weakref.WeakKeyDictionary()
_lock = _threading.RLock()


def _self_pipe_operation(event, args, frame):
    if _pair_code is None or frame.f_code is not _pair_code or frame.f_globals is not vars(_socket):
        return False
    caller = frame.f_back
    if caller is None or caller.f_code is not _pipe_code or caller.f_globals is not vars(_proactor):
        return False
    if not isinstance(caller.f_locals.get("self"), _proactor.BaseProactorEventLoop):
        return False
    try:
        sock, address = args
        listener = frame.f_locals["lsock"]
        if sock.family != _socket.AF_INET or sock.type != _socket.SOCK_STREAM or sock.proto != 0:
            return False
        identity = (_threading.get_ident(), id(frame))
        with _lock:
            if event == "socket.bind":
                if sock is not listener or address != ("127.0.0.1", 0):
                    return False
                if listener in _listeners:
                    return False
                _listeners[listener] = identity
                return True
            if event in {"socket.connect", "socket.connect_ex"}:
                if sock is not frame.f_locals.get("csock") or _listeners.get(listener) != identity:
                    return False
                endpoint = listener.getsockname()
                if endpoint[0] != "127.0.0.1" or endpoint[1] <= 0 or address != endpoint:
                    return False
                del _listeners[listener]  # One connection, no reusable port allowance.
                return True
    except (KeyError, TypeError, ValueError, OSError, AttributeError):
        return False
    return False


def offline_audit(event, args):
    if event in {"socket.connect", "socket.connect_ex", "socket.bind"}:
        if _self_pipe_operation(event, args, sys._getframe(1)):
            return
        raise RuntimeError("CI_OFFLINE_NETWORK_REFUSED")
    if event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr",
                 "socket.getnameinfo", "socket.sendto", "socket.sendmsg"}:
        raise RuntimeError("CI_OFFLINE_NETWORK_REFUSED")


sys.addaudithook(offline_audit)
