# Xray-Aimili

AimiliVPN 采用 Docker 安全网关部署：OpenVPN、Xray、代理网关和 Web 面板都运行在容器内，宿主机只保留 `ml` 管理菜单与 Docker 编排入口。

## 安装

```bash
curl -fsSL https://raw.githubusercontent.com/Aimilibot/Xray-Aimili/main/install-docker.sh | bash
```

兼容旧命令的 `install.sh` 也会转到 Docker 安装，不再安装宿主机 OpenVPN、Xray 或 systemd 网关服务。

Docker 一键脚本支持管道安装，会保留 `/opt/aimilivpn-docker/vpngate_data` 与 `.env` 后重新部署。安装完成后直接在终端输入 `ml` 进入独立管理菜单，无需进入容器。

如需重置 Docker 面板数据，显式使用：

```bash
AIMILI_RESET_DOCKER_DATA=1 bash install-docker.sh
```

## 管理

```bash
ml
ml status
ml logs
ml update
ml uninstall
```
