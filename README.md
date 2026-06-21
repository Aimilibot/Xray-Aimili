# Xray-Aimili

AimiliVPN 是 Docker 化的 VPN/Xray 网关面板。OpenVPN、Xray、VPNGate、WARP、自定义出站、策略路由和 Web 面板都运行在 Docker 容器内；宿主机只保留 Docker、持久化数据目录和 `ml` 管理菜单。

## 部署边界

- 网关服务不再运行在宿主机。
- 宿主机不安装宿主机版 OpenVPN/Xray 服务。
- 宿主机不创建 `aimilivpn.service` 网关服务。
- 宿主机只负责 Docker Compose 编排、`/dev/net/tun` 挂载和 `ml` 菜单入口。
- 容器内负责 OpenVPN、Xray、代理网关、Web 面板、策略路由和节点检测。

## 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/Aimilibot/Xray-Aimili/main/install-docker.sh | bash
```

兼容旧命令：

```bash
curl -fsSL https://raw.githubusercontent.com/Aimilibot/Xray-Aimili/main/install.sh | bash
```

`install.sh` 只会转到 Docker 安装，不会再部署宿主机网关服务。

## 安装位置

默认安装到：

```bash
/opt/aimilivpn-docker
```

持久化数据：

```bash
/opt/aimilivpn-docker/vpngate_data
```

Docker 环境配置：

```bash
/opt/aimilivpn-docker/.env
```

## 管理命令

安装完成后，在宿主机直接使用：

```bash
ml
ml status
ml logs
ml update
ml uninstall
```

不需要进入容器操作。

## 更新

```bash
ml update
```

更新会拉取代码、重建 Docker 镜像并重新启动 Docker Stack，默认保留 `.env` 和 `vpngate_data`。

## 重置数据

如需清空面板配置、节点、订阅、路由规则和运行数据：

```bash
AIMILI_RESET_DOCKER_DATA=1 bash install-docker.sh
```

默认安装和更新不会清空数据。

## Docker 运行要求

容器需要：

- Docker Engine
- Docker Compose plugin
- `/dev/net/tun`
- `NET_ADMIN` capability

`install-docker.sh` 会自动检测 Docker 和 `/dev/net/tun`，并生成 `ml` 菜单入口。

## 安全说明

当前项目以 Docker 网关为唯一正式运行方式。宿主机只作为管理入口，避免 OpenVPN、Xray 和策略路由直接散落在宿主机系统服务里，降低升级、清理和排障成本。
