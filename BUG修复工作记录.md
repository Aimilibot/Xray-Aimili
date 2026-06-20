# Bug 修复工作记录

日期：2026-06-20

## 本次目标

对当前仓库进行一次基础 bug 检查，修复确认存在的问题，并记录本次检查、修复与验证结果，方便预览和后续追踪。

补充检查目标：再次核对前端已经显示的功能是否都有后端能力支撑，重点修复“界面可见但实际不可用”的功能断点。

## 检查范围

- Python 后端代码：`backend/`、`cli/`、`proxy/`、`utils/`
- Web 前端脚本：`web/js/*.js`
- 安装与容器入口脚本：`install.sh`、`install-docker.sh`、`docker/full/entrypoint.sh`、`docker/vpngate/entrypoint.sh`
- 关键后端 API 路由：`backend/app/api/handler.py`

## 发现并修复的问题

### 1. 订阅节点开关接口可能重复写入 HTTP 响应

文件：`backend/app/api/handler.py`

接口：

```text
POST /api/panel/subscription-nodes/toggle
```

问题说明：

该接口在成功或失败写入 JSON 响应后缺少 `return`。请求处理会继续向下执行后续路由判断，最终可能再写入一次 `404 not found` 响应。

可能影响：

- 前端收到混杂或损坏的 HTTP 响应。
- 浏览器端表现为请求失败、JSON 解析失败，或状态切换后提示异常。
- 后端日志中可能出现一次请求对应异常响应行为。

修复方式：

在该接口分支结束处补充 `return`，确保响应发送后立即结束当前请求处理。

### 2. 安全后缀路由遇到查询参数时匹配失败

文件：`backend/app/api/handler.py`

问题说明：

`validate_path()` 之前用完整 `self.path` 剥离安全后缀。URL 带查询参数时，例如：

```text
/secret/api/xray/subscribe?token=abc
```

剥离后会得到 `/api/xray/subscribe?token=abc`，导致后续接口路径比较失败。

修复方式：

改为使用 `urllib.parse.urlparse(self.path).path` 做路由匹配和安全后缀剥离，查询参数仍保留在 `self.path` 中供具体接口读取。

### 3. 上游代理环境变量默认端口错误且非法端口会中断解析

文件：`utils/vpn.py`

问题说明：

无端口代理配置如 `http_proxy=http://127.0.0.1` 会被错误地默认到 `10808`；同时非法端口可能在读取 `parsed.port` 时抛出异常。

修复方式：

- SOCKS 默认端口改为 `1080`。
- HTTP 默认端口改为 `80`。
- HTTPS 默认端口改为 `443`。
- 支持 `[::1]:1081` 这类 IPv6 host:port 写法。
- 非法端口会被忽略，不再导致代理解析流程崩溃。

### 4. 自定义节点功能开关无法真正关闭

文件：`backend/app/db.py`

问题说明：

`custom_enabled` 虽然在前端和 API 中是可切换功能，但读取和保存 feature flags 时会被强制改回 `True`，导致关闭自定义节点功能后立即失效。

修复方式：

- 移除强制开启 `custom_enabled` 的逻辑。
- 修复默认值合并：缺失字段保留默认值，只有显式写入 `false` 才关闭对应功能。

### 5. 前端 inline 事件参数存在 JS 字符串注入风险

文件：

- `web/js/app.js`
- `web/js/sub.js`
- `web/js/outbound.js`
- `web/js/route.js`

问题说明：

部分按钮把后端返回的 `id` 或客户端名称拼进 inline `onclick`。原先只做 HTML 转义，但浏览器会在事件属性中解码 HTML 实体，包含单引号的值仍可能打断 JS 字符串，造成按钮失效或注入风险。

修复方式：

- 新增 `jsArg()`，用 JSON 字符串规则生成安全 JS 参数。
- inline 事件代码再经过 `esc()` 写入 HTML 属性。
- 覆盖订阅节点、出站节点、路由规则、VPNGate 节点测试/连接等按钮。

### 6. 独立订阅节点只显示记录，后端未生成可用入站

文件：

- `backend/app/core/xray.py`
- `web/index.html`
- `web/js/sub.js`

问题说明：

前端订阅节点弹窗支持取消“加入订阅链接”，并展示“独立节点”列表；但是后端保存节点时仍会强制挂到默认订阅，或者忽略独立节点自己的协议、端口和伪装域名。这样用户在前端能看到独立节点，却无法真正生成对应 Xray 入站。

修复方式：

- 后端支持 `add_to_subscription: false`，独立节点保存为 `subscription_id: ""`。
- 独立节点现在会校验自己的协议、端口、UUID、SOCKS5 账号密码和 VLESS-Reality 伪装域名。
- `write_xray_config()` 会为独立节点生成对应入站：
  - VLESS-Reality：生成独立 Reality 入站和密钥参数。
  - VMess + WS + TLS：使用匹配域名证书生成独立 TLS 入站，证书缺失时安全跳过。
  - SOCKS5：生成独立 SOCKS 入站。
- 路由规则使用独立节点 ID 时，现在可以匹配到真实 Xray inbound tag。
- 前端在“加入订阅链接”和“独立节点”之间切换时，会正确显示或隐藏端口、协议字段，并给出归属提示。

### 7. 自动默认订阅缺少可运行配置

文件：`backend/app/core/xray.py`

问题说明：

当用户直接创建节点且未选择订阅时，后端会自动创建“默认订阅”。此前默认订阅缺少端口、协议和伪装域名，后续生成 Xray 配置时会因为订阅端口为空而跳过。

修复方式：

默认订阅现在会带上可运行的基础配置：

```text
protocol: vless-reality
port: 10086
camouflage_host: www.microsoft.com
ws_path: /
```

### 8. 订阅端口与独立节点端口缺少双向冲突校验

文件：`backend/app/core/xray.py`

问题说明：

独立节点创建时已需要避开订阅端口，但反过来创建订阅时也必须避开已有独立节点端口。否则前端保存成功，Xray 启动时才会因为端口重复失败。

修复方式：

保存订阅链接时会检查已有独立节点端口；保存独立节点时会检查已有订阅链接端口和其他独立节点端口。

## 前端功能与后端接口核对

已扫描 `web/js/*.js` 中的前端 `fetch("./api/...")` 调用，并与 `backend/app/api/handler.py` 路由匹配：

- 前端识别到 41 个 API 请求。
- 后端识别到 54 个路由/路由前缀。
- 未发现前端已调用但后端缺失的 API。

本轮发现的主要问题不是“接口不存在”，而是“独立节点接口保存成功但没有完整后端行为”；已补齐保存校验、配置生成和前端字段联动。

## 第二轮专项修复

### 9. 节点池可用性与策略路由健康检查误导

文件：`backend/app/core/vpn.py`

修复内容：

- OpenVPN 未连接时，不再把 `table 100` 缺失直接报成策略路由异常；改为提示“连接成功后会自动配置”。
- 节点池存在但未检测时，提示用户执行检测或同步。
- 节点池全部不可用时，会带上最近一次探测失败原因，方便判断是 OpenVPN、TUN 权限、网络还是节点本身问题。

### 10. 路由规则创建会打断当前代理连接

文件：

- `backend/app/core/xray.py`
- `backend/app/api/handler.py`
- `web/index.html`
- `web/js/route.js`

修复内容：

- 新增“创建草稿”和“保存应用”两个动作。
- 创建草稿只写入规则，不立即重载 Xray，避免浏览器正通过本节点访问面板时请求被中断。
- 保存应用才同步到 Xray。
- 弹窗内增加提示：如果当前浏览器正在走本面板创建的代理，建议先关闭本机代理或切换直连后再保存应用。
- 入站选择现在包含订阅链接和节点条目，独立节点也能直接作为路由来源。

### 11. 节点明明绑定路由但显示“出站：未绑定”

文件：`web/js/sub.js`

修复内容：

节点卡片计算出站绑定时，现在同时检查自身节点 ID 和父订阅 ID。规则绑定到父订阅时，子节点列表不会再误显示“未绑定”。

### 12. 自定义节点不能导入订阅链接和 Shadowrocket 节点分享

文件：

- `backend/app/core/xray.py`
- `backend/app/api/handler.py`
- `web/js/outbound.js`

修复内容：

- 支持 Base64 多行订阅内容，自动取第一个可识别节点。
- 支持订阅 URL：后端会拉取订阅内容并解析第一个可用节点。
- 支持 Shadowrocket 常见分享格式，包括 `ss://`、`vmess://`、`vless://`、`trojan://`、`socks://`、`http://`。
- 保存出站节点时保留来源类型：`custom-node`、`subscription`、`json-config`。
- Xray 配置生成和节点测试现在支持这些导入类型，不再只认 `json-config`。

### 13. WARP 缺少刷新和删除功能

文件：

- `backend/app/core/xray.py`
- `backend/app/api/handler.py`
- `web/index.html`
- `web/js/outbound.js`

修复内容：

- 新增 WARP 删除接口。
- 新增 WARP 刷新接口，会重新注册并生成配置。
- 前端 WARP 面板新增刷新状态、刷新配置、删除配置按钮。
- 删除 WARP 时会关闭功能开关并同步路由规则。

### 14. 新建节点名称需要自动生成

文件：

- `web/js/outbound.js`
- `web/js/sub.js`

修复内容：

- 新建自定义出站节点时，自动填入类似 `NODE-US-LosAngeles-01` 的名称。
- 新建订阅节点时，自动填入类似 `SUB-US-LosAngeles-01` 的名称。
- 地区和城市优先使用浏览器语言与时区推断，用户仍可手动修改。

### 15. 首次打开自动切换白天/夜晚主题

文件：`web/index.html`

修复内容：

首次打开且没有保存过主题时，面板会按浏览器时间自动选择：

- 07:00 至 18:59：白天主题。
- 19:00 至次日 06:59：夜晚主题。

用户手动选择主题后，会继续使用用户保存的偏好。

### 16. 删除未引用的老旧 CSS

文件：`web/css/style.css`

检查结论：

- `web/index.html` 和 `web/login.html` 都只引用 `css/theme.css`。
- 项目内没有发现 `style.css` 或 `css/style.css` 的引用。
- `style.css` 有 2710 行，且包含大量与当前主题样式重复的旧选择器。

处理方式：

删除未引用的 `web/css/style.css`，保留当前实际加载的 `web/css/theme.css`。CSS 文件总行数从 3681 行减少到 972 行左右。

## 验证结果

已执行以下检查，均通过：

```bash
python3 -m unittest discover -s tests
PYTHONPYCACHEPREFIX=/tmp/xray-aimili-pycache-check python3 -m compileall -q backend cli proxy utils tests
for f in web/js/*.js; do node --check "$f" || exit 1; done
for f in install.sh install_cn.sh entrypoint.sh; do if [ -f "$f" ]; then bash -n "$f" || exit 1; fi; done
```

说明：

- 第一次 Python 编译检查曾因 macOS 用户缓存目录权限受限失败；改用 `PYTHONPYCACHEPREFIX=/tmp/xray-aimili-pycache-check` 将缓存定向到临时目录后，编译检查通过。
- 新增 21 个单元测试，覆盖代理环境变量解析、路由路径解析、功能开关读写、独立节点保存、默认订阅配置、端口冲突校验、独立节点 Xray 入站生成、订阅/Shadowrocket 导入、路由草稿保存、WARP 删除和策略路由健康检查。
- 本机当前未检测到 Docker CLI，因此未执行 `docker compose config` 校验。
- 本项目此前没有测试目录，本次已新增轻量级 `unittest` 测试。

## 变更文件

- `backend/app/api/handler.py`
- `backend/app/core/vpn.py`
- `backend/app/core/xray.py`
- `backend/app/db.py`
- `utils/vpn.py`
- `web/index.html`
- `web/css/style.css`
- `web/js/app.js`
- `web/js/sub.js`
- `web/js/outbound.js`
- `web/js/route.js`
- `tests/test_feature_flags.py`
- `tests/test_handler_paths.py`
- `tests/test_independent_subscription_nodes.py`
- `tests/test_routing_and_outbound_fixes.py`
- `tests/test_utils_vpn.py`
- `BUG修复工作记录.md`

## 后续建议

- 为 API handler 增加更完整的路由单元测试，重点覆盖每个 `POST` 分支只发送一次响应。
- 在 CI 中加入 Python 编译检查、前端 JS 语法检查和 Shell 脚本语法检查。
- 如果后续安装 Docker CLI，可补充执行 `docker compose config` 与容器启动配置校验。

## 建议增加的功能

### 1. 一键系统自检报告导出

把现有分层健康检查扩展成可下载报告，包含 API 源、节点池、OpenVPN、Xray、本地代理、DNS、路由表、端口占用和最近错误日志。这样用户遇到问题时可以直接导出报告给维护者。

### 2. 配置备份与恢复

提供一键导出和导入 `ui_auth.json`、订阅节点、出站节点、路由规则、feature flags。升级或迁移服务器时可以减少误操作。

### 3. Xray 端口冲突预检

保存订阅入口、普通入站和内部 API 入站前，统一检查端口是否重复或被系统占用，提前给出明确提示，而不是等 Xray 启动失败。

### 4. 操作审计日志

记录管理员执行的关键操作，例如启动/停止 OpenVPN、修改账号密码、修改端口、添加订阅、删除节点、重置流量。后续排障会清楚很多。

### 5. 节点质量评分与自动淘汰

对节点建立综合评分：最近成功率、延迟、带宽估计、国家地区、失败次数、黑名单状态。自动优先使用稳定节点，并淘汰连续失败节点。

### 6. 前端无 inline 事件重构

后续可以逐步把 `onclick="..."` 改为事件委托和 `data-*` 属性，进一步降低注入风险，也让前端代码更容易测试。
