"""Entry point: python -m lpl.main"""
import logging
import os

import uvicorn

from . import api, db, scanner
from .config import config


def check_writable() -> None:
    log = logging.getLogger("lpl")
    probe = config.data_dir / ".lpl-write-test"
    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        log.error("Cannot write to the data folder %s (%s). Running as uid %s. The folder probably belongs to root: "
                  "create it yourself before starting the container, or change its owner to this user.",
                  config.data_dir, e, os.getuid() if hasattr(os, "getuid") else "?")
        raise SystemExit(1)
    if config.local:
        return
    if not config.photo_root.is_dir():
        log.error("Photo folder %s not found: check the volume line in docker-compose.yml.", config.photo_root)
    elif not os.access(config.photo_root, os.W_OK):
        log.warning("Photo folder %s is read-only for this user: ratings, labels and tags can't be written to .xmp files. "
                    "Give the user read & write permission on the shared folder.", config.photo_root)


def create_app():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    check_writable()
    db.init()
    api.mount_web()
    if os.environ.get("LPL_NO_SCAN") != "1":
        scanner.start_background()
        from . import autotag
        autotag.start_background()
    if config.local:
        from . import localedit
        localedit.start_background()
    return api.app


def run():
    app = create_app()
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8080")),
                log_level=os.environ.get("LOG_LEVEL", "info").lower(), proxy_headers=True)


if __name__ == "__main__":
    run()
