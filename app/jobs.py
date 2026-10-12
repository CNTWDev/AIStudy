"""Renewable database leases shared by the worker and manual administrator runs."""
import logging
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta

from . import db

log = logging.getLogger(__name__)
_active = ContextVar("active_job_lease", default=None)


def check_active():
    task = _active.get()
    if task:
        task.check()


def later(seconds):
    return (datetime.fromisoformat(db.now()) + timedelta(seconds=seconds)).isoformat(timespec='seconds')


class Lease:
    def __init__(self, name, ttl):
        self.name, self.ttl, self.owner = name, ttl, uuid.uuid4().hex
        self.result = {}
        self.stopped = threading.Event()
        self.lost = threading.Event()

    def renew(self):
        return db.run('UPDATE jobs SET locked_until=? WHERE name=? AND owner=?', later(self.ttl), self.name, self.owner) == 1

    def heartbeat(self):
        try:
            while not self.stopped.wait(self.ttl / 3):
                if not self.renew():
                    self.lost.set()
                    return
        except Exception:
            self.lost.set()
            log.exception('job lease renewal failed: %s', self.name)
        finally:
            if not db.IS_PG:  # PostgreSQL pool belongs to the process, not this thread.
                db.close()

    def check(self):
        row = db.one("SELECT owner,locked_until FROM jobs WHERE name=?", self.name)
        if self.lost.is_set() or not row or row["owner"] != self.owner or row["locked_until"] < db.now():
            raise RuntimeError('Background job lease lost')


@contextmanager
def lease(name, ttl=180):
    task = Lease(name, ttl)
    db.run('INSERT INTO jobs(name) VALUES(?) ON CONFLICT(name) DO NOTHING', name)
    acquired = db.run("UPDATE jobs SET owner=?,locked_until=? WHERE name=? AND "
                      "(locked_until='' OR locked_until IS NULL OR locked_until<?)",
                      task.owner, later(ttl), name, db.now()) == 1
    if not acquired:
        yield None
        return
    heartbeat = threading.Thread(target=task.heartbeat, name='lease-' + name, daemon=True)
    heartbeat.start()
    failed = False
    token = _active.set(task)
    try:
        yield task
        task.check()
        failed = bool(task.result.get('error') or task.result.get('write_errors'))
    except BaseException as exc:
        failed = True
        task.result['error'] = type(exc).__name__
        raise
    finally:
        _active.reset(token)
        task.stopped.set()
        heartbeat.join(timeout=5)
        db.run("UPDATE jobs SET owner='',locked_until='',last_run=?,last_result=?,failures=CASE WHEN ?=1 THEN failures+1 ELSE 0 END "
               "WHERE name=? AND owner=?", db.now(), db.jdump(task.result), int(failed), name, task.owner)
