# AimiliVPN 开发进展与代码缺陷审查报告

本报告对 AimiliVPN 项目（包含后端主程序、Xray 与 OpenVPN 核心控制、本地代理网关、命令行管理终端及 Docker 部署脚本）进行了深入的代码级审查，评估了当前系统的开发进展，并详细列出了疑似存在的 Bug、潜在设计缺陷以及相应的优化建议。

---

## 📂 1. 项目架构与进展概述

目前项目具有清晰的模块化结构，各核心组件的开发已初具规模：

1. **核心后端控制台 (`/backend`)**:
   - 采用原生 Python 实现的高并发、多线程控制系统，完成了 OpenVPN 进程拉取、测速、心跳检测与 Xray 核心代理的分发控制。
   - 提供了一套基于 `BaseHTTPRequestHandler` 的轻量级 Web API 接口，负责与前端 UI 以及本地 CLI 终端通信。
2. **本地代理网关 (`/proxy`)**:
   - 独立实现的 HTTP/SOCKS5 双协议代理转换网关，监听 `7928`（默认）端口，可将客户端流量安全地桥接到 OpenVPN 创建的虚拟网卡 `tun0` 上。
   - 实现了基于策略的 DNS 解析（DNS over VPN）与出站套接字物理设备绑定。
3. **命令行终端终端 (`/cli`)**:
   - 提供了友好的 `ml` 全局命令行交互控制终端，能够实时拉取后端 API 展示系统指标、多线程测速、节点直连切换、日志查看和 BBR 一键配置。
4. **网页管理面板 (`/web`)**:
   - 原生 HTML/JS 实现的管理面板，基本打通了系统状态仪表盘、出站节点配置、路由分流管理和订阅下发逻辑。
5. **一键安装与部署脚本 (`/`)**:
   - `install-docker.sh` 和 `install.sh` 完成了 Docker 化一键拉取和系统级 Systemd 服务配置，并具备自动拉取 Xray 官方二进制和宿主机 TUN 网卡检测功能。

---

## 🔍 2. 疑似存在的 Bug 与安全缺陷

在对 Python 源码进行深层审计时，发现了以下几个影响系统稳定性和特定网络环境下可能导致服务崩溃/解析失败的 Bug：

### Bug 1: SOCKS5 代理响应解析截断与 TLS 握手崩溃
* **定位文件**: [vpn.py](file:///Users/hmily/开发者文档/Xray-Aimili/backend/app/core/vpn.py#L88-L112) 中的 `fetch_api_text_via_proxy` 函数。
* **缺陷代码**:
  ```python
  resp = s.recv(10)
  if len(resp) < 4 or resp[1] != 0:
      raise RuntimeError("SOCKS5 connection request rejected")
  if is_https:
      ctx = ssl.create_default_context() if use_ssl_verify else ssl._create_unverified_context()
      s = ctx.wrap_socket(s, server_hostname=domain)
  ```
* **原理解析**:
  SOCKS5 协议（RFC 1928）在连接请求建立成功后，代理服务器返回的响应长度是**可变的**，具体取决于绑定的地址类型（`ATYP`）：
  - `ATYP = 1` (IPv4 地址): 响应包长度为 `4 (Header) + 4 (IPv4) + 2 (Port) = 10` 字节。
  - `ATYP = 3` (域名): 响应包长度为 `4 (Header) + 1 (Length) + N (Domain) + 2 (Port)` 字节。
  - `ATYP = 4` (IPv6 地址): 响应包长度为 `4 (Header) + 16 (IPv6) + 2 (Port) = 22` 字节。
  
  很多主流 SOCKS5 代理在连接成功后会返回 `ATYP=4`（IPv6 绑定）或 `ATYP=3` 格式。代码中直接死等并只读取 `s.recv(10)`：
  1. 如果代理返回 IPv6（22字节），程序只读走了前10字节，**剩余的 12 字节留在 TCP 缓冲区中**。
  2. 随后如果 `is_https` 为真，程序立刻调用 `ssl.wrap_socket` 进行 TLS 握手。由于缓冲区中残留了刚才未读完的 12 字节 SOCKS5 握手回包，TLS 引擎会将这些数据误认为是 HTTPS 握手的 Server Hello 数据，进而引发 **TLS 握手协议错误 (如 `bad record mac` 或 `unknown protocol`)** 并导致整个 API 获取流程断开崩溃。
* **修复建议**:
  应当先读取前 4 个字节以解析 `ATYP`，然后根据 `ATYP` 动态读取剩余的地址和端口长度：
  ```python
  header = recv_exact(s, 4)
  atyp = header[3]
  if atyp == 1:
      recv_exact(s, 6) # 4 bytes IPv4 + 2 bytes Port
  elif atyp == 3:
      addr_len = recv_exact(s, 1)[0]
      recv_exact(s, addr_len + 2)
  elif atyp == 4:
      recv_exact(s, 18) # 16 bytes IPv6 + 2 bytes Port
  ```

---

### Bug 2: `main.py` 端口配置处理不一致与环境变量回退失效
* **定位文件**: [main.py](file:///Users/hmily/开发者文档/Xray-Aimili/backend/app/main.py#L73-L77) 与 [main.py](file:///Users/hmily/开发者文档/Xray-Aimili/backend/app/main.py#L171-L172)。
* **缺陷代码**:
  在 `main()` 启动初期（第 74-77 行）：
  ```python
  try:
      ui_port = int(ui_cfg.get("port", UI_PORT))
  except (TypeError, ValueError):
      ui_port = int(os.environ.get("UI_PORT", "8787"))
  ```
  但是在启动 `DualStackHTTPServer` 之前（第 171-172 行）：
  ```python
  ui_host = ui_cfg.get("host", UI_HOST)
  ui_port = int(ui_cfg.get("port", UI_PORT))  # ⚠️ 此处直接转换可能抛出异常且忽略了上面处理好的 ui_port 变量
  ...
  DualStackHTTPServer((ui_host, ui_port), Handler).serve_forever()
  ```
* **原理解析**:
  在第 77 行中，代码已经考虑到了 `ui_cfg` 中可能不存在 `port` 或其非数字的情况，并正确利用 `try-except` 捕获异常并回退到环境变量或默认值 `8787`，保存在局部变量 `ui_port` 中。
  然而，在第 172 行中，程序重新声明了 `ui_port` 并直接执行 `int(ui_cfg.get("port", UI_PORT))`，由于没有 `try-except` 保护，一旦 `ui_auth.json` 中配置异常，服务会**在启动的最后关头直接崩溃**。并且，即使第一步从环境变量成功加载了 `ui_port`，也会在这里被 `ui_cfg.get` 覆盖掉，导致外部设置的 `UI_PORT` 环境变量失效。
* **修复建议**:
  删除第 171-173 行的多余重复读取，直接复用 `main()` 顶部已经安全解析完成的局部变量 `ui_host` 和 `ui_port`。

---

### Bug 3: `get_upstream_proxy` 解析无端口的环境变量时丢失代理
* **定位文件**: [vpn.py](file:///Users/hmily/开发者文档/Xray-Aimili/utils/vpn.py#L125-L128)。
* **缺陷代码**:
  ```python
  if "://" in val:
      parsed = urllib.parse.urlsplit(val)
      ptype = "socks" if parsed.scheme.startswith("socks") else "http"
      if parsed.hostname and parsed.port:
          return ptype, parsed.hostname, parsed.port
  ```
* **原理解析**:
  如果系统配置的代理环境变量类似 `http_proxy=http://127.0.0.1` 或是 `socks5://127.0.0.1`（省略了默认端口），`urllib.parse.urlsplit` 能够解析出 `hostname="127.0.0.1"`，但其 `port` 属性为 `None`。
  由于使用了严格的 `if parsed.hostname and parsed.port:` 判断，该代理配置会**被整个静默跳过**，系统无法启用任何代理，而实际上应该自动使用协议的默认端口（HTTP 使用 80 / SOCKS 使用 1080）。
* **修复建议**:
  在 `parsed.port` 为空时，根据 `scheme` 赋予其默认端口：
  ```python
  if parsed.hostname:
      port = parsed.port or (1080 if ptype == "socks" else 80)
      return ptype, parsed.hostname, port
  ```

---

### Bug 4: 节点并发测试时的临时配置文件竞争与覆盖
* **定位文件**: [vpn.py](file:///Users/hmily/开发者文档/Xray-Aimili/backend/app/core/vpn.py#L658-L660) 和 `test_worker` 线程函数中。
* **缺陷代码**:
  ```python
  config_file = str(node["config_file"]) # 路径形式如 vpngate_data/configs/node-127-0-0-1-1194.ovpn
  ...
  temp_path = Path(config_file)
  temp_path.write_text(config_text, encoding="utf-8")
  ...
  # 触发测试连接后删除
  temp_path.unlink()
  ```
* **原理解析**:
  当后台健康检查线程（`background_proxy_checker`）和用户在 Web 端或命令行中手动点击测速/连接同一个节点时，多个线程会并发访问 `test_node_by_id` 或 `test_worker`。
  因为节点的 `config_file` 路径是根据节点 IP 和端口静态生成的，多个线程会**同时写入同一个文件并执行删除**。一旦线程 A 启动了 OpenVPN，在尚未完全读取完毕时，线程 B 测试结束执行了 `temp_path.unlink()`，将导致线程 A 的 OpenVPN 进程因“配置文件不存在/被删除”而启动失败，或者在写入过程中产生数据截断。
* **修复建议**:
  在进行临时测速和测试连接时，应使用带随机后缀或线程 ID 的临时文件名，或者在内存中建立临时的配置缓存，避免直接竞争修改同一静态文件。

---

### Bug 5: 物理网卡与路由接口硬编码限制
* **定位模块**: 本地代理网关 `proxy/server.py` 与策略路由设置。
* **原理解析**:
  网关中绑定出站流量到 `tun0` 网卡是通过 `setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"tun0")` 完成的。
  如果宿主机存在多个 OpenVPN 实例（例如用户自建了其他 VPN 占用了 `tun0`，导致本程序 OpenVPN 自动被分配为 `tun1` 或 `tun2`），代理网关由于硬编码了 `tun0`，将**无法将流量正确导入 VPN 隧道**，导致本地代理网关彻底失效，甚至连接报错 `No such device`。
* **修复建议**:
  策略路由以及 `SO_BINDTODEVICE` 中的网卡名称应该根据当前活动的 OpenVPN 连接动态获取（例如从 OpenVPN 启动参数或 `ip route` 结果中解析），而不是在代码中硬编码 `tun0`。

---

## 🛠️ 3. 后续优化与演进建议

为了进一步提升 AimiliVPN 的生产环境稳定性，建议在接下来的开发中落实以下功能：

1. **分层健康检测模型 (Layered Health Model)**:
   - 细化当前仪表盘的健康指标，区分：**API 源通畅度**、**节点池可用比例**、**OpenVPN 网卡状态**、**策略路由健康度**、**本地代理连通性**。在出口检测失败时，明确给出是因为 DNS 污染、TLS 被干扰、TUN 驱动缺失还是端口占用。
2. **多平台兼容与安全降级**:
   - `SO_BINDTODEVICE` 和策略路由依赖 Linux 特有的内核接口。对于非 Linux 系统（如开发者的 macOS 环境），应提供基于“上游 Socks5 动态转发”或“普通网关代理模式”的友好兼容降级，防止在开发调试阶段出现大量系统调用崩溃。
3. **OpenVPN 账号密码文件安全性**:
   - 目前 `vpngate_auth.txt` 直接以明文写入磁盘。虽然文件权限在创建时被设置为 `0600`，但在容器挂载卷或共享环境中仍存在暴露风险。建议对本地凭据进行加密存储，或者仅在内存中通过管道形式传递给 OpenVPN 进程。

---

本报告已写入项目文档中 [dev-progress-and-bugs.zh.md](file:///Users/hmily/%E5%BC%80%E5%8F%91%E8%80%85%E6%96%87%E6%A1%A3/Xray-Aimili/docs/dev-progress-and-bugs.zh.md)，方便团队成员查阅与开展后续修复工作。
