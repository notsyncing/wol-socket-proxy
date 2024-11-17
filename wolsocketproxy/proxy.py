import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from logging import Logger
from typing import Any, Literal, override

import wakeonlan

from wolsocketproxy.monitor import Monitor

WOL_MAX_WAIT_COUNT = 60


@dataclass
class ProxyRoute:
    local_address: str
    local_port: int
    target_address: str
    target_port: int
    protocol: Literal["tcp", "udp"]


@dataclass
class ProxyConfig:
    routes: list[ProxyRoute]
    mac_mappings: dict[str, str]


class ProxyUdpProtocol(asyncio.DatagramProtocol):
    _proxy: "Proxy"
    _monitor: Monitor
    _transport: asyncio.transports.DatagramTransport
    _target_address: str
    _target_port: int
    _target_pair: tuple[str, int]

    def __init__(self, proxy: "Proxy", monitor: Monitor, target_address: str, target_port: int) -> None:
        self._proxy = proxy
        self._monitor = monitor
        self._target_address = target_address
        self._target_port = target_port
        self._target_pair = (target_address, target_port)

    @override
    def connection_made(self, transport: asyncio.transports.DatagramTransport) -> None:     # type: ignore[override]
        self._transport = transport

    @override
    def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        if addr == self._target_address:
            return

        if not self._monitor.is_available(self._target_address):
            self._proxy._wake_up_target(self._target_address)  # noqa: SLF001

        self._transport.sendto(data, self._target_pair)


class Proxy:
    _log: Logger = logging.getLogger()
    _config: ProxyConfig
    _monitor: Monitor
    _routes: list[ProxyRoute]
    _mac_mappings: dict[str, str]

    def __init__(self, config: ProxyConfig) -> None:
        self._config = config
        self._routes = config.routes
        self._mac_mappings = config.mac_mappings

        target_ip_set = set()

        for r in self._routes:
            target_ip_set.add(r.target_address)

        self._monitor = Monitor(watching_ip_list=target_ip_set)

    def start(self) -> None:
        self._monitor.start()

        for route in self._routes:
            self.__create_route(route)

        loop = asyncio.get_event_loop()

        self._log.info("Proxy server started.")

        with contextlib.suppress(KeyboardInterrupt):
            loop.run_forever()

        self._log.info("Proxy server is stopping...")

        loop.close()

    def __create_route(self, route: ProxyRoute) -> None:
        if route.protocol == "tcp":
            self.__create_tcp_route(route)
        elif route.protocol == "udp":
            self.__create_udp_route(route)
        else:
            raise ValueError(
                f"Unsupported protocol {route.protocol} in route from {route.local_address}:{route.local_port} to "
                f"{route.target_address}:{route.target_port}"
            )

    def __create_tcp_route(self, route: ProxyRoute) -> None:
        cr = asyncio.start_server(
            self.__make_tcp_route_handler(route.target_address, route.target_port),
            route.local_address,
            route.local_port,
        )

        loop = asyncio.get_event_loop()
        loop.run_until_complete(cr)

        self._log.info(
            f"Created {route.protocol} proxy from {route.local_address}:{route.local_port} to "
            f"{route.target_address}:{route.target_port}"
        )

    async def __pipe(self, target_address: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while not reader.at_eof():
                writer.write(await reader.read(2048))
        except ConnectionResetError:
            self._log.warning("Connection reset by target %s", target_address)
        finally:
            writer.close()

    def __make_tcp_route_handler(self, target_address: str, target_port: int) -> Any:   # noqa: ANN401
        async def handler(local_reader: asyncio.StreamReader, local_writer: asyncio.StreamWriter) -> None:
            if not self._monitor.is_available(target_address):
                self._wake_up_target(target_address)

            try:
                target_reader, target_writer = await asyncio.open_connection(target_address, target_port)
            except OSError as e:
                self._monitor.report_availablity(target_address, False)
                self._log.error("Unable to open connection to %s:%d", target_address, target_port)
                raise e

            send_pipe = self.__pipe(target_address, local_reader, target_writer)
            recv_pipe = self.__pipe(target_address, target_reader, local_writer)
            await asyncio.gather(send_pipe, recv_pipe)

        return handler

    def __create_udp_route(self, route: ProxyRoute) -> None:
        loop = asyncio.get_event_loop()

        cr = loop.create_datagram_endpoint(
            lambda: ProxyUdpProtocol(self, self._monitor, route.target_address, route.target_port),
            local_addr=(route.local_address, route.local_port),
        )

        loop.run_until_complete(cr)

    def _wake_up_target(self, target_address: str) -> None:
        target_mac_addr = self._mac_mappings[target_address]

        self._log.info("Waking up target %s at %s", target_address, target_mac_addr)

        wakeonlan.send_magic_packet(target_mac_addr, interface=target_address)

        count = 0

        while not self._monitor.is_available(target_address):
            count = count + 1
            time.sleep(1)

            if count > WOL_MAX_WAIT_COUNT:
                self._log.warning("Target %s still not online after %d seconds!", target_address, count)
                raise ConnectionAbortedError(f"Target {target_address} does not wake up")
