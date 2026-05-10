import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime
from threading import Thread
from typing import Literal

from aiohttp import web
from aiohttp.web import Request, Response
from setproctitle import setproctitle


@dataclass
class KeepAliveConfig:
    listen_address: str = "127.0.0.1"
    listen_port: int = 18080

    watchdog_feed_interval: int = 600

    keep_alive_method: Literal["special_process"] = "special_process"

    special_process_name: str | None = "wsp-keepalive"


class KeepAliveDaemon:
    _log: logging.Logger = logging.getLogger(__name__)

    _config: KeepAliveConfig
    _web_app: web.Application

    _watchdog_last_feed_time: datetime
    _watchdog_is_hungry: bool = False

    _special_process_id: int = -1

    def __init__(self, config: KeepAliveConfig) -> None:
        self._config = config

        self._web_app = web.Application()

        self._web_app.add_routes(
            [
                web.get("/watchdog/feed", self._handle_watchdog_feed)
            ]
        )

    async def _watchdog_timer(self) -> None:
        while True:
            previous_is_hungry = self._watchdog_is_hungry
            last_feed_interval = datetime.now() - self._watchdog_last_feed_time

            if last_feed_interval.total_seconds() > self._config.watchdog_feed_interval:
                self._watchdog_is_hungry = True
            else:
                self._watchdog_is_hungry = False

            if not previous_is_hungry and self._watchdog_is_hungry:
                Thread(target=self._on_watchdog_hungry, daemon=True).start()
            elif previous_is_hungry and not self._watchdog_is_hungry:
                Thread(target=self._on_watchdog_feed, daemon=True).start()

            await asyncio.sleep(1)

    def _on_watchdog_hungry(self) -> None:
        self._log.warning("Watchdog is hungry!")

        if self._special_process_id > 0:
            os.kill(self._special_process_id, signal.SIGKILL)
            os.waitpid(self._special_process_id, 0)
            self._log.warning("Killed special process PID %d", self._special_process_id)
            self._special_process_id = -1

    def _on_watchdog_feed(self) -> None:
        self._log.info("Watchdog is feed.")

        if self._config.keep_alive_method == "special_process" and self._special_process_id < 0:
            self._start_special_process()

    def _special_process(self) -> None:
        assert self._config.special_process_name is not None
        setproctitle(self._config.special_process_name)

        while True:
            time.sleep(60)

            if os.getppid() == 1:
                os._exit(0)

    def _start_special_process(self) -> bool:
        self._special_process_id = os.fork()

        if self._special_process_id == 0:
            self._special_process()
            return True

        self._log.info(
            "Started special process with name %s, PID %d",
            self._config.special_process_name, self._special_process_id
        )

        return False

    def start(self) -> None:
        self._watchdog_last_feed_time = datetime.now()
        self._watchdog_is_hungry = False

        if self._config.keep_alive_method == "special_process":  # noqa: SIM102
            if self._start_special_process():
                return

        loop = asyncio.new_event_loop()

        def _start_loop() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        Thread(target=_start_loop, daemon=True).start()

        watchdog_task = asyncio.run_coroutine_threadsafe(self._watchdog_timer(), loop)

        self._log.info(
            "Keep-alive daemon started at %s:%d, watchdog feed interval %ds.",
            self._config.listen_address, self._config.listen_port, self._config.watchdog_feed_interval
        )

        web.run_app(self._web_app, host=self._config.listen_address, port=self._config.listen_port)

        self._log.info("Stopping...")

        watchdog_task.cancel()
        loop.stop()

        if self._special_process_id > 0:
            os.kill(self._special_process_id, signal.SIGKILL)
            os.waitpid(self._special_process_id, 0)

        self._log.info("Stopped.")

    async def _handle_watchdog_feed(self, _: Request) -> Response:
        last_feed = self._watchdog_last_feed_time
        self._watchdog_last_feed_time = datetime.now()
        interval = (self._watchdog_last_feed_time - last_feed).total_seconds()

        return web.json_response(
            {
                "status": "ok",
                "last_feed": last_feed.strftime("%Y-%m-%d %H:%M:%S"),
                "interval": interval,
            }
        )
