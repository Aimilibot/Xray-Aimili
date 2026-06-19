# Xray-Aimili

- Docker 版本

  ```bash
  curl -fsSL https://raw.githubusercontent.com/Aimilibot/Xray-Aimili/main/install-docker.sh | bash
  ```

  Docker 一键脚本支持管道安装，会保留 `/opt/aimilivpn-docker/vpngate_data` 与 `.env` 后重新部署。安装完成后直接在终端输入 `ml` 进入独立管理菜单，无需进入容器。
  如需重置 Docker 面板数据，显式使用 `AIMILI_RESET_DOCKER_DATA=1`。

- Linux 版本

  ```bash
  curl -fsSL https://raw.githubusercontent.com/Aimilibot/Xray-Aimili/main/install.sh | bash
  ```

安装完成：

```bash
ml
ml status
ml logs
ml update
ml uninstall
```
