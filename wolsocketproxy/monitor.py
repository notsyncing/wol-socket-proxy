import logging
import time
from collections.abc import Collection
from logging import Logger
from threading import Thread

from icmplib import multiping


class Monitor:
    _log: Logger = logging.getLogger()
    _ip_state: dict[str, bool]

    def __init__(self, watching_ip_list: Collection[str]) -> None:
        self._ip_state = {}

        for ip in watching_ip_list:
            self._ip_state[ip] = False

    def start(self) -> None:
        t = Thread(target=self.__check_ip_state)
        t.daemon = True
        t.start()

    def __check_ip_state(self) -> None:
        self._log.info("IP monitor is started.")

        while True:
            ip_list = self._ip_state.keys()
            results = multiping(ip_list, count=3, timeout=2, privileged=False)

            for result in results:
                original_state = self._ip_state[result.address]
                self._ip_state[result.address] = result.is_alive

                if original_state != result.is_alive:
                    if original_state is False:
                        self._log.info("Target %s now online.", result.address)
                    else:
                        self._log.info("Target %s now offline.", result.address)

            time.sleep(1)

    def report_availablity(self, ip: str, available: bool) -> None:
        self._ip_state[ip] = available

    def is_available(self, ip: str) -> bool:
        return self._ip_state.get(ip, False)
