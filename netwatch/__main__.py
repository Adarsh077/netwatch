import logging
import os
import signal
import threading

from .checks import monitor_loop
from .config import Config
from .db import Database
from .server import serve
from .speedtest import speedtest_loop


def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    cfg = Config()
    db = Database(cfg.db_path)
    stop = threading.Event()

    threading.Thread(target=monitor_loop, args=(db, cfg, stop), name="monitor", daemon=True).start()
    if cfg.speedtest_enabled:
        threading.Thread(target=speedtest_loop, args=(db, cfg, stop), name="speedtest", daemon=True).start()

    httpd = serve(db, cfg)

    def shutdown(*_):
        stop.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    httpd.serve_forever()
    logging.getLogger("netwatch").info("stopped")


if __name__ == "__main__":
    main()
