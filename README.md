# Xray-Aimili

- Docker 版本

  ```bash
  curl -fsSL https://raw.githubusercontent.com/Aimilibot/Xray-Aimili/main/install-docker.sh | bash
  ```

  Docker 一键脚本支持管道安装，会自动清理 `/opt/aimilivpn-docker` 旧 Docker 面板残留后重新部署。安装完成后直接在终端输入 `ml` 进入独立管理菜单，无需进入容器。

- Linux 版本

  ```bash
  curl -fsSL https://raw.githubusercontent.com/Aimilibot/Xray-Aimili/main/install.sh | bash
  ```

安装完成后可使用：

```bash
ml
ml status
ml logs
ml update
ml uninstall
```
