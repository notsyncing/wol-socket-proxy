# wol-socket-proxy

A socket proxy with wake-on-lan and IPMI power control feature.

It can forward TCP(bidirectional) and UDP(send-only) traffic from local machine to remote machine, and:

- send a magic packet to wake it (wake-on-lan) 
- invoke IPMI power on to wake it

before forwarding traffic if the remote machine does not respond to ICMP ping or some HTTP URL.

## Requirements

Python 3.12+

The remote machine must accept and respond to ICMP ping requests if you use `ping` method.

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

Finally, run it:

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

    // Define machines
    "machines": {
        "some-server": {
            "ip_address": "192.168.1.124",                              // the IP address of this machine, optional
            "mac_address": "11:22:33:44:55:66",                         // The MAC address of this machine, optional
            "wake_up_method": "ipmi",                                   // How to wake up this machine
                                                                        //   "ipmi" - You must specify "ipmi_config_name"
                                                                        //   "wol" - You must specify "mac_address"
            "ipmi_config_name": "some-server-ipmi",                     // IPMI config of this machine, must match what defined in "ipmi_configs"
            "online_check_method": "http",                              // How to check this machine is online
                                                                        //   "http" - You must specify "online_check_http_url"
                                                                        //   "ping" - You must specify "ip_address"
            "online_check_http_url": "http://192.168.1.124/health",     // The URL used to check if machine is online, optional
            "online_check_timeout": 300,                                // Timeout when checking machine is online, seconds, optional, default is 60
            "online_check_http_expected_code": 200,                     // Expected HTTP status code when using "http" method, optional, default is 200
            "ipmi_force_reset_if_power_up_failed": true,                // Use IPMI power reset if this machine is not online after timeout, optional, default is false
            "ipmi_max_reset_try_count": 3,                              // Max IPMI power reset retry count before giving-up, optional, default is 3
            "keep_alive_mode": true,                                    // Indicates whether this machine has a keep-alive daemon, optional, default is false
                                                                         // If you enable this, wolsocketproxy will send a request when there is any traffic
            "keep_alive_mode_base_url": "http://192.168.1.124:8080",     // Keep-alive daemon provided URL, optional
            "scheduled_power_up_times": [                               // Scheduled power-up times, optional
                {
                    "cron": "0 7 * * 1-5",                             // Cron expression for auto power-up
                    "keep_alive_time": 7200                             // Send keep-alive requests for N seconds after power-up
                }
            ]
        },
        // ... more machines ...
    },

    // Define forwarding routes
    "routes": [
        {
            "local_address": "0.0.0.0",              // The local address to listen
            "local_port": 12345,                     // The local port to listen
            "target_machine_name": "some-server",    // The target machine name, must match what defined in "machines"
            "target_address": "192.168.1.124",       // The target address forwarding to
            "target_port": 12345,                    // The target port forwarding to
            "protocol": "tcp"                        // The protocol to use, can choose "tcp" or "udp"
        },
        // ... more routes ...
    ],

    // Define IPMI configs, optional
    "ipmi_configs": [
        {
            "name": "some-server-ipmi",                 // IPMI config name
            "redfish_url": "https://192.168.1.123/",    // IPMI Redfish API base URL (should not contain /redfish/v1)
            "username": "admin",                        // IPMI Redfish API login username
            "password": "123456"                        // IPMI Redfish API login password
        },
        // ... more IPMI configs ...
    ]
}
```

## Additional modes

### Keep-alive daemon mode

This mode is used for running on target machine with auto-suspend (e.g. running `circadian` or similar), to keep the target
machine from auto suspending when running tasks.

Use the following command on the target machine to enter this mode:

```bash
wolsocketproxy -k
```

The config file `wolsocketproxy.conf` should look like the following:


```json5
// Keep-alive daemon mode
{
    "listen_address": "0.0.0.0",                // The address to listen for HTTP server, default is 127.0.0.1
    "listen_port": 18080,                       // The port to listen for HTTP server, default is 18080
    "watchdog_feed_interval": 60,               // Watchdog feed interval, seconds, default is 600
    "keep_alive_method": "special_process",     // How to keep target machine alive, default is "special_process"
                                                //   "special_process" - Fork a child process with specified name
    "special_process_name": "wsp-keepalive",    // The display name of the forked child process, default is "wsp-keepalive"
}
```

You need to periodically send an HTTP GET request to `/watchdog/feed` at the `listen_port` in `watchdog_feed_interval` time,
or the special process will be killed.
You can combine this mode with `circadian`'s `process_block` config to keep it from auto-suspending.

If you enable `keep_alive_mode` in proxy mode's config, the proxy will send requests to `/watchdog/feed` of this machine whenever there is any traffic towards this machine through it.

### Scheduled power-up

You can configure `scheduled_power_up_times` in each machine entry to automatically power up the machine at specified times using
a cron expression. After the machine comes online, the proxy sends keep-alive requests to the machine's keep-alive daemon
(for the duration specified by `keep_alive_time`) to prevent it from auto-suspending during the expected usage window.

Each entry in `scheduled_power_up_times` contains:
- `cron`: A standard 5-field cron expression (e.g. `"0 7 * * 1-5"` for weekdays at 7:00)
- `keep_alive_time`: Duration in seconds to send keep-alive requests after power-up (e.g. `7200` for 2 hours)

If multiple entries overlap in time, the keep-alive period is automatically extended to cover the longest window, and no
duplicate keep-alive requests are sent.
