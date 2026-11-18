"""Entry point: python -m lpl.main"""
import logging
import os

import uvicorn

from . import api, db, scanner
from .config import config


def create_app():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    db.init()
    api.mount_web()
    if os.environ.get("LPL_NO_SCAN") != "1":
        scanner.start_background()
    return api.app


def run():
    app = create_app()
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8080")),
                log_level=os.environ.get("LOG_LEVEL", "info").lower(), proxy_headers=True)


if __name__ == "__main__":
    run()
