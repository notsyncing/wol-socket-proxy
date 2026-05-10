import json
import logging
from argparse import ArgumentParser
from pathlib import Path
from sys import stdout

import dataclass_wizard

from wolsocketproxy.keepalive import KeepAliveConfig, KeepAliveDaemon
from wolsocketproxy.proxy import Proxy, ProxyConfig


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(
                stream=stdout,
            ),
        ],
        force=True,
    )

    log = logging.getLogger()

    parser = ArgumentParser(prog="wolsocketproxy", description="A socket proxy with wake-on-lan feature.")

    parser.add_argument(
        "-k",
        "--keep-alive",
        dest="keep_alive_mode",
        default=False,
        action="store_true",
        help="Start in keep-alive daemon mode",
    )

    parser.add_argument(
        "-c",
        "--config",
        help="The config file to use, default lookup order: /etc/wolsocketproxy.conf, ./wolsocketproxy.conf",
    )

    args = parser.parse_args()
    config_path: Path

    if "config" in args and args.config is not None:
        config_path = Path(args.config)
    else:
        config_path = Path("/etc/wolsocketproxy.conf")

        if not config_path.exists():
            config_path = Path("./wolsocketproxy.conf")

    if not config_path.exists():
        log.error("Config file path %s does not exist!", config_path)
        return

    config_data = json.loads(config_path.read_text())

    if args.keep_alive_mode:
        config = dataclass_wizard.fromdict(KeepAliveConfig, config_data)

        log.info("Loaded keep-alive mode config from %s", config_path)

        keep_alive_daemon = KeepAliveDaemon(config)
        keep_alive_daemon.start()
    else:
        config = dataclass_wizard.fromdict(ProxyConfig, config_data)

        log.info("Loaded proxy mode config from %s", config_path)

        proxy = Proxy(config)
        proxy.start()
