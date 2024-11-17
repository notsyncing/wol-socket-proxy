# wol-socket-proxy

A socket proxy with wake-on-lan feature.

It can forward TCP(bidirectional) and UDP(send-only) traffic from local machine to remote machine, and send a magic packet to wake it (wake-on-lan) before forwarding traffic if the remote machine does not respond to ICMP ping.

## Requirements

Python 3.11+

The remote machine must accept and respond to ICMP ping requests.

## Usage

You can install it from PyPI or use a prebuilt docker image.

### Install from PyPI

Simply install it:

```
pip install wolsocketproxy
```

Then create a config file:

```
cp wolsocketproxy.conf.example /etc/wolsocketproxy.conf
```

Modify the config file as your requirements.

Finally, run it

```
wolsocketproxy
```

### Use prebuilt docker image

See `docker/docker-compose.yml` for more details.

## Config

The `wolsocketproxy.conf` has the following structure:

```json5
{
    // NOTE: Comments are not allowed in actual config file
    // Define forwarding routes
    "routes": [
        {
            "local_address": "0.0.0.0",         // The local address to listen
            "local_port": 12345,                // The local port to listen
            "target_address": "192.168.0.100",  // The target address forwarding to
            "target_port": 22,                  // The target port forwarding to
            "protocol": "tcp"                   // The protocol to use
        },
        // ... more routes ...
    ],

    // Tell the program about the MAC address of each IP in routes
    "mac_mappings": {
        // Key is IP address, and value is MAC address, case-insensitive
        "192.168.0.100": "11:22:33:44:55:66",
        // ... more items ...
    }
}
```
