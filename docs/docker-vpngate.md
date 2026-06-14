# AimiliVPN Docker Deployment

AimiliVPN now provides two Docker modes:

- **full panel**: recommended one-click deployment. It runs the Web panel, Xray, VPNGate/OpenVPN, the local HTTP/SOCKS proxy, WARP outbound support, and custom outbound routing in one container.
- **vpngate-only**: advanced deployment. It runs only the VPNGate/OpenVPN exit service and local proxy, while another panel or service consumes it as an upstream proxy.

## Full Panel Mode

Use the Docker installer on a Linux VPS:

```bash
sudo bash install-docker.sh
```

The installer will:

- install Docker Engine and the Docker Compose plugin when missing;
- deploy the project to `/opt/aimilivpn-docker`;
- create `/opt/aimilivpn-docker/.env`;
- start the stack with `docker compose up -d --build`;
- print the panel URL, username, and password.

Manual start from the project directory:

```bash
docker compose up -d --build
```

Useful commands:

```bash
docker compose ps
docker compose logs -f
docker compose restart
docker compose down
```

### Full Panel Ports

Default ports:

```text
8787 -> Web panel
7928 -> HTTP/SOCKS proxy
```

You can change them in `.env`:

```env
UI_PORT=8787
LOCAL_PROXY_PORT=7928
SECRET_PATH=change_me
UI_USERNAME=admin
UI_PASSWORD=change_me
```

Xray inbound ports created in the panel must also be published by Docker. Add them to `docker-compose.yml` before starting or restarting the stack:

```yaml
ports:
  - "${UI_PORT:-8787}:${UI_PORT:-8787}"
  - "${LOCAL_PROXY_PORT:-7928}:${LOCAL_PROXY_PORT:-7928}"
  - "10086:10086"
```

Then restart:

```bash
docker compose up -d
```

### Data

Runtime data is stored on the host in:

```text
./vpngate_data
```

The container maps it to:

```text
/data
```

This persists panel login config, VPNGate nodes, OpenVPN configs, subscription nodes, routing rules, logs, traffic history, and Xray config.

### Required Docker Permissions

OpenVPN needs a TUN device and network administration capability. The default compose file uses:

```yaml
cap_add:
  - NET_ADMIN
devices:
  - /dev/net/tun:/dev/net/tun
```

It does not use `privileged` by default.

If the container logs say `/dev/net/tun` is missing, check the host:

```bash
ls -l /dev/net/tun
```

On some VPS images, load TUN manually:

```bash
modprobe tun
mkdir -p /dev/net
mknod /dev/net/tun c 10 200
chmod 600 /dev/net/tun
```

If your provider blocks TUN devices inside containers, use a VPS image/provider that supports TUN. As a last-resort troubleshooting step, you can temporarily test with `privileged: true`, but it is not the recommended default.

## VPNGate-Only Mode

This mode is kept for advanced gateway deployments:

```bash
cd docker
docker compose -f docker-compose.vpngate.yml up -d --build
```

Open:

```text
http://SERVER_IP:8787/
```

Use the proxy:

```text
http://SERVER_IP:7928
socks5://SERVER_IP:7928
```

The compose file sets:

```text
AIMILI_SERVICE_MODE=vpngate
```

In this mode, Xray startup is skipped and the UI hides the full subscription/routing workspace. The service focuses on VPNGate, OpenVPN, and the local proxy.
