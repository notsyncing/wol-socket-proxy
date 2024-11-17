import json
import logging
from argparse import ArgumentParser
from pathlib import Path

import dataclass_wizard

from wolsocketproxy.proxy import Proxy, ProxyConfig


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger()

    parser = ArgumentParser(prog="wolsocketproxy", description="A socket proxy with wake-on-lan feature.")

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
    config = dataclass_wizard.fromdict(ProxyConfig, config_data)

    log.info("Loaded config from %s", config_path)

    proxy = Proxy(config)
    proxy.start()
