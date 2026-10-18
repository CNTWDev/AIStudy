"""Independent, restartable worker: python -m app.worker [--once]."""
import logging
import signal
import sys
import threading
import time

from . import bank, bankflow, config, curriculum_admin, db, engine, evidence, news
from .catalog import catalog
from .content import content
from .methods import methods

log = logging.getLogger(__name__)


def main(once=False):
    logging.basicConfig(level=logging.INFO)
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    db.init()
    curriculum_admin.refresh(force=True)
    content.load()
    methods.load()
    engine.load_seed_items(config.SEED_DIR)
    engine.load_seed_lessons(config.SEED_DIR)
    bank.sync()
    evidence.ensure_current()
    next_bank = next_news = 0.0
    try:
        while not stopped.is_set():
            curriculum_admin.refresh()
            now = time.monotonic()
            for name, run, due in [('bank', bankflow.run_once, next_bank), ('news', news.run_daily, next_news)]:
                if now < due or stopped.is_set():
                    continue
                try:
                    log.info('%s: %s', name, run())
                except Exception:
                    log.exception('%s failed; retry on next scheduled run', name)
                if name == 'bank':
                    next_bank = now + bankflow.INTERVAL
                else:
                    next_news = now + 20 * 60
            if once:
                break
            stopped.wait(30)
    finally:
        db.close()


if __name__ == '__main__':
    main('--once' in sys.argv)
