import asyncio
import logging
from dataclasses import dataclass
from logging import Logger
from threading import Thread
from typing import Literal

from aiohttp import ClientConnectionError, ClientSession, ClientTimeout
from icmplib.multiping import async_multiping


@dataclass
class MonitorConfig:
    online_check_method: Literal["ping", "http"] = "ping"
    online_check_ip_address: str | None = None
    online_check_http_url: str | None = None
    online_check_http_expected_code: int = 200
    online_check_timeout: int = 60


class Monitor:
    _log: Logger = logging.getLogger()
    _monitor_configs: dict[str, MonitorConfig]
    _machine_states: dict[str, bool]
    _stop: bool = False

    def __init__(self, watching_machines: dict[str, MonitorConfig]) -> None:
        self._monitor_configs = {}
        self._machine_states = {}

        for name, config in watching_machines.items():
            self._monitor_configs[name] = config
            self._machine_states[name] = False

    def start(self) -> None:
        def _start() -> None:
            asyncio.run(self.__check_machine_states())

        t = Thread(target=_start, daemon=True)
        t.start()

    def stop(self) -> None:
        self._stop = True

    async def __check_http_urls(self, configs: dict[str, MonitorConfig]) -> dict[str, bool]:
        if len(configs) <= 0:
            return {}

        async def fetch(session: ClientSession, url: str, timeout: ClientTimeout) -> int:  # noqa: ASYNC109
            try:
                async with session.get(url, timeout=timeout) as resp:
                    return resp.status
            except ClientConnectionError:
                return -1

        results = {}
        result_names = []
        result_futures = []

        async with ClientSession(timeout=ClientTimeout(total=30)) as session:
            for name, conf in configs.items():
                assert conf.online_check_method == "http"
                assert conf.online_check_http_url is not None

                resp_future = fetch(
                    session,
                    conf.online_check_http_url,
                    timeout=ClientTimeout(total=conf.online_check_timeout)
                )

                result_names.append(name)
                result_futures.append(resp_future)

            http_results = await asyncio.gather(*result_futures)

            for i, http_result in enumerate(http_results):
                results[result_names[i]] = http_result == conf.online_check_http_expected_code

        return results

    async def __check_machine_states(self) -> None:
        self._log.info("Monitor started.")

        while not self._stop:
            ping_ip_list = [
                conf.online_check_ip_address
                for conf in self._monitor_configs.values()
                if conf.online_check_method == "ping"
            ]

            if len(ping_ip_list) > 0:
                ping_results_future = async_multiping(ping_ip_list, count=3, timeout=2, privileged=False)
            else:
                ping_results_future = asyncio.Future()
                ping_results_future.set_result([])

            http_results_future = self.__check_http_urls(
                configs={
                    name: conf for name, conf in self._monitor_configs.items() if conf.online_check_method == "http"
                }
            )

            ping_results, http_results = await asyncio.gather(ping_results_future, http_results_future)

            for result in ping_results:
                original_state = self._machine_states[result.address]
                self._machine_states[result.address] = result.is_alive

                if original_state != result.is_alive:
                    if original_state is False:
                        self._log.info("Target %s now online by ping.", result.address)
                    else:
                        self._log.info("Target %s now offline by ping.", result.address)

            for ip, result in http_results.items():
                original_state = self._machine_states[ip]
                self._machine_states[ip] = result

                if original_state != result:
                    if original_state is False:
                        self._log.info("Target %s now online by http.", ip)
                    else:
                        self._log.info("Target %s now offline by http.", ip)

            await asyncio.sleep(1)

    def report_availablity(self, machine_name: str, available: bool) -> None:
        self._machine_states[machine_name] = available

    def is_available(self, machine_name: str) -> bool:
        return self._machine_states.get(machine_name, False)
