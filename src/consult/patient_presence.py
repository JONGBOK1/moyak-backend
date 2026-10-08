"""Ephemeral patient-page presence for the single-process local demo.

No database migration; restart clears presence until the next heartbeat.
Use a shared TTL store before running multiple server workers.
"""
from threading import Lock
from time import monotonic

TTL = 60
_seen = {}
_lock = Lock()


def touch(cid):
    with _lock:
        now = monotonic()
        for key in list(_seen):
            if now - _seen[key] >= TTL:
                del _seen[key]
        _seen[cid] = now


def online(cid):
    with _lock:
        stamp = _seen.get(cid)
        return stamp is not None and monotonic() - stamp < TTL
