import asyncio
import contextlib
import logging
from dataclasses import dataclass
from logging import Logger
from threading import Thread
from typing import Any, Literal, override

import aiohttp
import wakeonlan
from redfish.rest.v1 import HttpClient, redfish_client

from wolsocketproxy.common import URL_WATCHDOG_FEED
from wolsocketproxy.monitor import Monitor, MonitorConfig
from wolsocketproxy.utils import perform_ipmi_action


@dataclass
class MachineConfig:
    wake_up_method: Literal["ipmi", "wol"] = "wol"

    ip_address: str | None = None
    mac_address: str | None = None

    ipmi_config_name: str | None = None
    ipmi_force_reset_if_power_up_failed: bool = False
    ipmi_max_reset_try_count: int = 3
    ipmi_reset_retried_count: int = 0

    online_check_method: Literal["ping", "http"] = "ping"
    online_check_http_url: str | None = None
    online_check_http_expected_code: int = 200
    online_check_timeout: int = 60

    keep_alive_mode: bool = False
    keep_alive_mode_base_url: str | None = None
    keep_alive_min_interval: int = 1


@dataclass
class ProxyRoute:
    local_address: str
    local_port: int
    target_address: str
    target_port: int
    protocol: Literal["tcp", "udp"]
    target_machine_name: str | None = None


@dataclass
class IPMIConfig:
    name: str
    redfish_url: str
    username: str
    password: str


@dataclass
class ProxyConfig:
    routes: list[ProxyRoute]
    machines: dict[str, MachineConfig] | None = None
    mac_mappings: dict[str, str] | None = None  # Deprecated, use machines[n].mac_address instead
    ipmi_configs: list[IPMIConfig] | None = None


class TargetKeepAliveSender:
    _log: logging.Logger = logging.getLogger(__name__)

    _target_url: str
    _keep_alive_min_interval: int
    _loop: asyncio.AbstractEventLoop
    _queue: asyncio.Queue

    def __init__(self, target_base_url: str, keep_alive_min_interval: int) -> None:
        self._target_url = target_base_url.removesuffix("/") + URL_WATCHDOG_FEED
        self._keep_alive_min_interval = keep_alive_min_interval

        self._loop = asyncio.new_event_loop()
        self._queue = asyncio.Queue(1)

        def _loop() -> None:
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._send_worker())

        Thread(target=_loop, daemon=True).start()

    async def _send_worker(self) -> None:
        while True:
            await self._queue.get()

            try:
                async with aiohttp.request(
                    "GET",
                    self._target_url,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    await resp.json()
            except (aiohttp.ClientError, aiohttp.ClientResponseError):
                self._log.warning("Failed to send target keep alive request to %s", self._target_url, exc_info=True)

            await asyncio.sleep(self._keep_alive_min_interval)

    def schedule_send(self) -> None:
        def _no_exception_put() -> None:
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(1)

        self._loop.call_soon_threadsafe(_no_exception_put)


class ProxyUdpProtocol(asyncio.DatagramProtocol):
    _proxy: "Proxy"
    _monitor: Monitor
    _transport: asyncio.transports.DatagramTransport
    _target_machine_name: str
    _target_address: str
    _target_port: int
    _target_keep_alive_sender: TargetKeepAliveSender | None = None
    _target_pair: tuple[str, int]

    def __init__(
        self,
        proxy: "Proxy",
        monitor: Monitor,
        target_machine_name: str,
        target_address: str,
        target_port: int,
        target_keep_alive_sender: TargetKeepAliveSender | None = None,
    ) -> None:
        self._proxy = proxy
        self._monitor = monitor
        self._target_machine_name = target_machine_name
        self._target_address = target_address
        self._target_port = target_port
        self._target_keep_alive_sender = target_keep_alive_sender
        self._target_pair = (target_address, target_port)

    @override
    def connection_made(self, transport: asyncio.transports.DatagramTransport) -> None:  # type: ignore[override]
        self._transport = transport

    @override
    def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        asyncio.create_task(self.handle_datagram(data, addr))  # noqa: RUF006

    async def handle_datagram(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        if addr == self._target_address:
            return

        if not self._monitor.is_available(self._target_machine_name):
            await self._proxy._wake_up_target(self._target_machine_name)  # noqa: SLF001

        self._transport.sendto(data, self._target_pair)

        if self._target_keep_alive_sender is not None:
            self._target_keep_alive_sender.schedule_send()


class Proxy:
    _log: Logger = logging.getLogger()
    _config: ProxyConfig
    _monitor: Monitor
    _machines: dict[str, MachineConfig]
    _routes: list[ProxyRoute]
    _ipmi_configs: dict[str, IPMIConfig]

    def __init__(self, config: ProxyConfig) -> None:
        self._config = config
        self._machines = config.machines or {}
        self._routes = config.routes

        if config.mac_mappings is not None:
            for ip, mac in config.mac_mappings.items():
                if ip in self._machines:
                    machine = self._machines[ip]

                    if machine.mac_address is None:
                        machine.mac_address = mac
                else:
                    self._machines[ip] = MachineConfig(
                        wake_up_method="wol",
                        ip_address=ip,
                        mac_address=mac,
                    )

        self._ipmi_configs = {}

        if config.ipmi_configs is not None:
            for c in config.ipmi_configs:
                self._ipmi_configs[c.name] = c

        self._monitor = Monitor(
            watching_machines={
                name: MonitorConfig(
                    online_check_method=m.online_check_method,
                    online_check_ip_address=m.ip_address,
                    online_check_http_url=m.online_check_http_url,
                    online_check_http_expected_code=m.online_check_http_expected_code,
                    online_check_timeout=m.online_check_timeout,
                )
                for name, m in self._machines.items()
            }
        )

    def start(self) -> None:
        self._monitor.start()

        for route in self._routes:
            self.__create_route(route)

        loop = asyncio.get_event_loop()

        self._log.info("Proxy server started.")

        with contextlib.suppress(KeyboardInterrupt):
            loop.run_forever()

        self._log.info("Proxy server is stopping...")

        self._monitor.stop()

        loop.close()

    def __create_route(self, route: ProxyRoute) -> None:
        if route.target_machine_name is None:
            route.target_machine_name = route.target_address

        if route.protocol == "tcp":
            self.__create_tcp_route(route)
        elif route.protocol == "udp":
            self.__create_udp_route(route)
        else:
            raise ValueError(
                f"Unsupported protocol {route.protocol} in route from {route.local_address}:{route.local_port} to "
                f"{route.target_address}:{route.target_port}"
            )

        self._log.info(
            f"Created {route.protocol} proxy for machine {route.target_machine_name} "
            f"from {route.local_address}:{route.local_port} to "
            f"{route.target_address}:{route.target_port}"
        )

    def __create_tcp_route(self, route: ProxyRoute) -> None:
        assert route.target_machine_name is not None
        machine_config = self._machines[route.target_machine_name]
        target_keep_alive_sender = None

        if machine_config.keep_alive_mode:
            assert machine_config.keep_alive_mode_base_url is not None

            target_keep_alive_sender = TargetKeepAliveSender(
                machine_config.keep_alive_mode_base_url, machine_config.keep_alive_min_interval
            )

        cr = asyncio.start_server(
            self.__make_tcp_route_handler(
                route.target_machine_name,
                route.target_address,
                route.target_port,
                target_keep_alive_sender,
            ),
            route.local_address,
            route.local_port,
        )

        loop = asyncio.get_event_loop()
        loop.run_until_complete(cr)

    async def __pipe(
        self,
        target_address: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        target_keep_alive_sender: TargetKeepAliveSender | None = None,
    ) -> None:
        try:
            while not reader.at_eof():
                writer.write(await reader.read(2048))

                if target_keep_alive_sender is not None:
                    target_keep_alive_sender.schedule_send()
        except ConnectionResetError:
            self._log.warning("Connection reset by target %s", target_address)
        finally:
            writer.close()

    def __make_tcp_route_handler(
        self,
        target_machine_name: str,
        target_address: str,
        target_port: int,
        target_keep_alive_sender: TargetKeepAliveSender | None = None,
    ) -> Any:  # noqa: ANN401
        async def handler(local_reader: asyncio.StreamReader, local_writer: asyncio.StreamWriter) -> None:
            if not self._monitor.is_available(target_machine_name):
                await self._wake_up_target(target_machine_name)

            try:
                target_reader, target_writer = await asyncio.open_connection(target_address, target_port)
            except OSError as e:
                self._monitor.report_availablity(target_machine_name, False)
                self._log.error("Unable to open connection to %s:%d", target_address, target_port)
                raise e

            send_pipe = self.__pipe(target_address, local_reader, target_writer, target_keep_alive_sender)
            recv_pipe = self.__pipe(target_address, target_reader, local_writer, target_keep_alive_sender)
            await asyncio.gather(send_pipe, recv_pipe)

        return handler

    def __create_udp_route(self, route: ProxyRoute) -> None:
        target_machine_name = route.target_machine_name
        assert target_machine_name is not None

        machine_config = self._machines[target_machine_name]
        target_keep_alive_sender = None

        if machine_config.keep_alive_mode:
            assert machine_config.keep_alive_mode_base_url is not None

            target_keep_alive_sender = TargetKeepAliveSender(
                machine_config.keep_alive_mode_base_url, machine_config.keep_alive_min_interval
            )

        loop = asyncio.get_event_loop()

        cr = loop.create_datagram_endpoint(
            lambda: ProxyUdpProtocol(
                self,
                self._monitor,
                target_machine_name,
                route.target_address,
                route.target_port,
                target_keep_alive_sender,
            ),
            local_addr=(route.local_address, route.local_port),
        )

        loop.run_until_complete(cr)

    async def _wake_up_target(self, target_machine_name: str) -> None:
        machine = self._machines[target_machine_name]

        match machine.wake_up_method:
            case "ipmi":
                self._log.info("Waking up target %s by IPMI config %s", target_machine_name, machine.ipmi_config_name)
                assert machine.ipmi_config_name is not None

                await self.__wake_up_by_ipmi(target_machine_name, machine)

            case "wol":
                self._log.info("Waking up target %s at %s by WoL", target_machine_name, machine.mac_address)
                assert machine.mac_address is not None

                await asyncio.to_thread(wakeonlan.send_magic_packet, machine.mac_address)

            case _:
                raise ValueError(f"Unsupported wake up method {machine.wake_up_method} in target {target_machine_name}")

        count = 0

        while not self._monitor.is_available(target_machine_name):
            count = count + 1
            await asyncio.sleep(1)

            if count > machine.online_check_timeout:
                self._log.warning("Target %s still not online after %d seconds!", target_machine_name, count)
                break

        if not self._monitor.is_available(target_machine_name):
            if machine.wake_up_method == "ipmi" and machine.ipmi_force_reset_if_power_up_failed:
                if machine.ipmi_reset_retried_count > machine.ipmi_max_reset_try_count:
                    self._log.error(
                        "Target %s still not online after %d IPMI resets, will not retry!",
                        target_machine_name,
                        machine.ipmi_reset_retried_count,
                    )

                    raise ConnectionAbortedError(f"Target {target_machine_name} does not wake up!")

                machine.ipmi_reset_retried_count += 1

                self._log.warning("Will force reset target %s through IPMI, try #%d", machine.ipmi_reset_retried_count)

                await self.__reset_by_ipmi(target_machine_name, machine)

                await self._wake_up_target(target_machine_name)

            raise ConnectionAbortedError(f"Target {target_machine_name} does not wake up!")

        machine.ipmi_reset_retried_count = 0

    async def __call_reset_on_ipmi(self, ipmi_config: IPMIConfig, reset_type: str) -> None:
        ipmi_client: HttpClient = redfish_client(
            base_url=ipmi_config.redfish_url,
            username=ipmi_config.username,
            password=ipmi_config.password,
            timeout=60,
        )

        try:
            await asyncio.to_thread(ipmi_client.login, auth="session")

            await perform_ipmi_action(
                ipmi_client, "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset", body={"ResetType": reset_type}
            )
        finally:
            await asyncio.to_thread(ipmi_client.logout)

    async def __wake_up_by_ipmi(self, target_address: str, machine: MachineConfig) -> None:
        assert machine.ipmi_config_name is not None

        ipmi_config = self._ipmi_configs[machine.ipmi_config_name]

        await self.__call_reset_on_ipmi(ipmi_config, "On")

        self._log.info("Target %s power-on succeeded, waiting for online...", target_address)

    async def __reset_by_ipmi(self, target_address: str, machine: MachineConfig) -> None:
        assert machine.ipmi_config_name is not None

        ipmi_config = self._ipmi_configs[machine.ipmi_config_name]

        await self.__call_reset_on_ipmi(ipmi_config, "PowerCycle")

        self._log.info("Target %s power-cycle succeeded, waiting for online...", target_address)
