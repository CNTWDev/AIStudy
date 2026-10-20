"""Independent, restartable worker: python -m app.worker [--once]."""
import logging
import signal
import sys
import threading
import time

from . import bank, bankflow, config, curriculum_admin, db, engine, evidence, mailer, news
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
    # 每个后台任务多久跑一次（秒）；mail：到点写每周报告、发队列里的信（app/mailer.py）
    jobs = [('bank', bankflow.run_once, bankflow.INTERVAL), ('news', news.run_daily, 20 * 60), ('mail', mailer.run, 2 * 60)]
    due = {name: 0.0 for name, _, _ in jobs}
    try:
        while not stopped.is_set():
            curriculum_admin.refresh()
            now = time.monotonic()
            for name, run, every in jobs:
                if now < due[name] or stopped.is_set():
                    continue
                try:
                    log.info('%s: %s', name, run())
                except Exception:
                    log.exception('%s failed; retry on next scheduled run', name)
                due[name] = now + every
            if once:
                break
            stopped.wait(30)
    finally:
        db.close()


if __name__ == '__main__':
    main('--once' in sys.argv)
