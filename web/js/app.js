
        function showToast(message, type = "info") {
            let container = document.getElementById("toast-container");
            if (!container) {
                container = document.createElement("div");
                container.id = "toast-container";
                document.body.appendChild(container);
            }

            const toast = document.createElement("div");
            toast.className = `toast toast-${type}`;
            toast.textContent = message;
            container.appendChild(toast);

            setTimeout(() => {
                toast.classList.add("toast-leave");
                setTimeout(() => toast.remove(), 220);
            }, 2600);
        }

        function copyToClipboard(text) {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                return navigator.clipboard.writeText(text);
            }
            return new Promise((resolve, reject) => {
                try {
                    const textArea = document.createElement("textarea");
                    textArea.value = text;
                    textArea.style.position = "fixed";
                    textArea.style.top = "0";
                    textArea.style.left = "0";
                    textArea.style.opacity = "0";
                    document.body.appendChild(textArea);
                    textArea.focus();
                    textArea.select();
                    const successful = document.execCommand('copy');
                    document.body.removeChild(textArea);
                    if (successful) resolve();
                    else reject(new Error("execCommand copy failed"));
                } catch (err) {
                    reject(err);
                }
            });
        }

        async function copyShareText(id) {
            const input = document.getElementById(id);
            if (!input) return;
            try {
                await copyToClipboard(input.value);
                const btn = input.nextElementSibling;
                const oldText = btn.innerText;
                btn.innerText = "已复制";
                btn.style.background = "var(--green)";
                setTimeout(() => {
                    btn.innerText = oldText;
                    btn.style.background = "";
                }, 1500);
            } catch (err) {
                showToast("复制失败", "error");
            }
        }

        let nodes = [], state = {}, stats_cache = null;
        let featureGates = {
            vpngate_enabled: false,
            warp_enabled: false,
            custom_enabled: true
        };
        let currentPage = 1;
        const pageSize = 11;
        let currentPageNodes = [];

        const $ = id => document.getElementById(id);
        const esc = s => String(s || "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));
        const jsArg = s => JSON.stringify(String(s ?? ""));
        const base = p => (p || "").split(/[\/]/).pop();
        const formatDatePickerDate = (ts) => {
            if (!ts || ts <= 0) return "";
            const d = new Date(ts * 1000);
            return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
        };

        const translateQuality = q => {
            const dict = { "normal": "普通", "proxy": "代理", "datacenter": "数据中心", "mobile": "移动端" };
            return dict[q] || q || "-";
        };

        const translateIpType = t => {
            const dict = { "residential": "住宅 IP", "hosting": "机房 IP", "mobile": "移动网", "proxy": "代理 IP" };
            return dict[t] || t || "-";
        };

        const translateCountry = c => {
            const dict = {
                "Japan": "日本", "Korea Republic of": "韩国", "Korea": "韩国", "Republic of Korea": "韩国",
                "Thailand": "泰国", "United States": "美国", "United Kingdom": "英国", "Russian Federation": "俄罗斯",
                "Russian": "俄罗斯", "Viet Nam": "越南", "Vietnam": "越南", "China": "中国", "Taiwan": "台湾",
                "Taiwan Province of China": "台湾", "Hong Kong": "香港", "Singapore": "新加坡", "Malaysia": "马来西亚",
                "Indonesia": "印度尼西亚", "India": "印度", "Philippines": "菲律宾", "Australia": "澳大利亚",
                "New Zealand": "新西兰", "Canada": "加拿大", "Ukraine": "乌克兰", "France": "法国", "Germany": "德国",
                "Netherlands": "荷兰", "Sweden": "瑞典", "Norway": "挪威", "Spain": "西班牙", "Turkey": "土耳其",
                "South Africa": "南非", "Brazil": "巴西", "Argentina": "阿根廷", "Chile": "智利", "Mexico": "墨西哥",
                "Egypt": "埃及", "Romania": "罗马尼亚", "Poland": "波兰", "Kazakhstan": "哈萨克斯坦", "Georgia": "格鲁吉亚",
                "Mongolia": "蒙古", "Saudi Arabia": "沙特阿拉伯", "Iran": "伊朗", "Iraq": "伊拉克", "Colombia": "哥伦比亚",
                "Cambodia": "柬埔寨", "Ireland": "爱尔兰", "Italy": "意大利", "Switzerland": "瑞士", "Belgium": "比利时",
                "Austria": "奥地利", "Denmark": "丹麦", "Finland": "芬兰", "Portugal": "葡萄牙", "Greece": "希腊",
                "Czech Republic": "捷克", "Hungary": "匈牙利", "Israel": "以色列", "United Arab Emirates": "阿联酋",
                "UAE": "阿联酋", "Macao": "澳门", "Macau": "澳门", "Iceland": "冰岛", "Luxembourg": "卢森堡"
            };
            return dict[c] || c || "-";
        };

        const translateStatus = s => {
            const dict = { "available": "可用", "unavailable": "不可用", "not_checked": "待检测" };
            return dict[s] || s || "待检测";
        };

        function getLatencyClass(ms) {
            if (!ms) return '';
            if (ms < 50) return 'latency-good';
            if (ms < 150) return 'latency-medium';
            return 'latency-poor';
        }

        function syncFeatureGates(nextFlags) {
            if (nextFlags && typeof nextFlags === "object") {
                const flagValue = key => Object.prototype.hasOwnProperty.call(nextFlags, key)
                    ? nextFlags[key] === true
                    : featureGates[key] === true;
                featureGates = {
                    ...featureGates,
                    vpngate_enabled: flagValue("vpngate_enabled"),
                    warp_enabled: flagValue("warp_enabled"),
                    custom_enabled: flagValue("custom_enabled")
                };
                state.feature_flags = featureGates;
            }
            renderFeatureGateSwitches();
        }

        function isFeatureEnabled(key) {
            const flags = (state && state.feature_flags) || featureGates || {};
            return flags[key] === true;
        }

        function renderFeatureGateSwitches() {
            Object.keys(featureGates).forEach(key => {
                document.querySelectorAll(`[data-feature-toggle="${key}"]`).forEach(input => {
                    input.checked = featureGates[key] === true;
                });
                const card = document.querySelector(`[data-feature-card="${key}"]`);
                if (card) card.classList.toggle("is-enabled", featureGates[key] === true);
                const powerControl = document.querySelector(`[data-feature-power="${key}"]`);
                if (powerControl) {
                    const enabled = featureGates[key] === true;
                    powerControl.classList.toggle("is-on", enabled);
                    powerControl.setAttribute("aria-pressed", enabled ? "true" : "false");
                    powerControl.title = enabled ? "关闭" : "启动";
                    const label = powerControl.querySelector(".feature-power-label");
                    if (label) label.textContent = enabled ? "关闭" : "启动";
                }
            });
        }

        async function loadFeatureGates() {
            try {
                const res = await fetch("./api/features");
                const data = await res.json();
                if (data && data.features) syncFeatureGates(data.features);
            } catch (e) {
                renderFeatureGateSwitches();
            }
        }


        async function toggleFeaturePower(key) {
            await setFeatureGate(key, !isFeatureEnabled(key));
        }

        async function setFeatureGate(key, enabled) {
            const inputs = Array.from(document.querySelectorAll(`[data-feature-toggle="${key}"]`));
            inputs.forEach(input => input.disabled = true);
            const powerControl = document.querySelector(`[data-feature-power="${key}"]`);
            if (powerControl) powerControl.disabled = true;
            try {
                const res = await fetch("./api/features/toggle", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ key, enabled })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    showToast(data.error || "功能开关更新失败", "error");
                    inputs.forEach(input => input.checked = !enabled);
                    return;
                }
                syncFeatureGates(data.features);
                showToast(data.message || "功能开关已更新", "success");
                await load();
                if (key === "warp_enabled" && typeof loadWarpState === "function") await loadWarpState();
                if (key === "custom_enabled" && typeof loadOutboundNodes === "function") await loadOutboundNodes();
                if (key === "vpngate_enabled" && enabled) startConnectionPolling();
            } catch (e) {
                showToast("功能开关请求失败", "error");
                inputs.forEach(input => input.checked = !enabled);
            } finally {
                inputs.forEach(input => input.disabled = false);
                if (powerControl) powerControl.disabled = false;
                renderFeatureGateSwitches();
                if (typeof renderWarpPowerButton === "function") renderWarpPowerButton();
            }
        }

        async function load() {
            try {
                const r = await fetch("./api/nodes");
                const d = await r.json();
                nodes = d.nodes || [];
                state = d.state || {};
                syncFeatureGates(state.feature_flags || d.features);
                stableSortNodes();
                updateCountryFilter();
                render();
                fetchStats();
                populateSettingsForms();

                if (state.is_connecting) {
                    startConnectionPolling();
                }
            } catch (e) { }
        }

        async function logoutAdmin() {
            try {
                const res = await fetch("./api/logout", { method: "POST" });
                if (res.ok) {
                    window.location.reload();
                }
            } catch (err) {
                console.error("退出登录失败", err);
                window.location.reload();
            }
        }

        window.addEventListener('resize', () => {
            if (stats_cache && stats_cache.traffic_history) {
                drawTrafficChart(stats_cache.traffic_history);
            }
        });

        function initApp() {
            loadFeatureGates();
            load();

            setInterval(async () => {
                if (typeof state !== "undefined" && !state.is_connecting && document.visibilityState === "visible") {
                    try {
                        const r = await fetch("./api/nodes");
                    const d = await r.json();
                    nodes = d.nodes || [];
                    state = d.state || {};
                    syncFeatureGates(state.feature_flags || d.features);
                    stableSortNodes();
                        render();
                        fetchStats();
                    } catch (e) { }
                }
            }, 10000);

            setInterval(() => {
                if (document.visibilityState === "visible") {
                    fetchStats();
                }
            }, 2000);

            const savedTab = sessionStorage.getItem('currentTab') || 'tab-host';
            showTab(savedTab);

            document.querySelectorAll('input[name="theme"]').forEach(input => {
                input.addEventListener('change', () => {
                    if (input.checked) {
                        localStorage.setItem('theme', input.id);
                        document.querySelectorAll('.theme-option').forEach(card => {
                            if (card.getAttribute('for') === input.id) {
                                card.classList.add('active');
                            } else {
                                card.classList.remove('active');
                            }
                        });
                    }
                });
            });
        }

        window.addEventListener('DOMContentLoaded', initApp);

        function renderLayeredHealthGeneric(data, containerId, useNumbers = false, showErrors = false) {
            const container = document.getElementById(containerId);
            if (!container) return;
            
            const layers = [
                { key: "api_connectivity", name: useNumbers ? "1. API 源通畅度" : "API 源通畅度" },
                { key: "node_pool", name: useNumbers ? "2. 节点池可用性" : "节点池可用性" },
                { key: "openvpn_interface", name: useNumbers ? "3. OpenVPN网卡状态" : "OpenVPN网卡" },
                { key: "policy_routing", name: useNumbers ? "4. 策略路由健康度" : "策略路由" },
                { key: "local_proxy", name: useNumbers ? "5. 本地代理连通性" : "本地代理出口" }
            ];
            
            container.innerHTML = layers.map(layer => {
                const info = data[layer.key] || { ok: false, details: "未检测" };
                const isNotReady = info.details === "未启用" || info.details === "未启动" || info.details === "未检测" || info.details === "OpenVPN 连接未启动" || info.details === "Xray 代理服务未运行" || info.details.includes("未运行");
                const isSkip = info.details.includes("免检") || info.details.includes("跳过") || info.details.includes("无需配置");
                
                let badgeClass = "unavailable";
                let badgeText = "异常";
                if (info.ok) { badgeClass = "available"; badgeText = "正常"; }
                else if (isNotReady) { badgeClass = "not_checked"; badgeText = "未就绪"; }
                else if (isSkip && !useNumbers) { badgeClass = "not_checked"; badgeText = "跳过"; }
                
                const statusPulse = info.ok ? '<span class="badge-pulse"></span>' : '';
                
                let errorHtml = "";
                if (showErrors && !info.ok && info.error_type) {
                    let desc = "";
                    if (info.error_type === "PORT_COLLISION") desc = "诊断原因: 检测到本地代理端口被其他进程占用。请运行 `lsof -i :7928` 查找并结束冲突进程。";
                    else if (info.error_type === "DNS_POLLUTION") desc = "诊断原因: 本地 DNS 解析失败或返回了错误的 GFW 污染 IP。建议修改系统 DNS 为 8.8.8.8 等干净的公共解析器，或使用网关内置的 SOCKS5h 远程域名解析。";
                    else if (info.error_type === "TLS_INTERFERENCE") desc = "诊断原因: TCP 隧道已接通但 TLS 证书安全握手遭防火墙审查或阻断。说明该节点的 TLS 混淆特征失效，请尝试同步更新到其他公益节点。";
                    else if (info.error_type === "TUN_DRIVER_MISSING") desc = "诊断原因: 系统未找到 `/dev/net/tun` 设备。对于 Docker 部署，请确保使用 `--device=/dev/net/tun` 挂载，并拥有 NET_ADMIN 特权；主机环境请使用 `modprobe tun` 加载内核驱动。";
                    else if (info.error_type === "RP_FILTER_STRICT") desc = "诊断原因: 内核严格反向路径过滤 rp_filter 被启用，导致 VPN 网卡回包被丢弃。请在主机执行 `sysctl -w net.ipv4.conf.all.rp_filter=2`。";
                    if (desc) {
                        errorHtml = `
                            <div style="font-size: 12px; color: var(--red); background: var(--red-soft); border: 1px solid rgba(255, 69, 58, 0.15); border-radius: 6px; padding: 8px 12px; margin-top: 6px; line-height: 1.45;">
                                ${esc(desc)}
                            </div>
                        `;
                    }
                }
                
                return `
                    <div class="glass" style="background: var(--control); border-radius: var(--radius); padding: 14px 18px; display: flex; flex-direction: column; gap: 6px; border: 1px solid var(--border);">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <strong style="font-size: ${useNumbers ? '14.5px' : '12px'}; color: ${useNumbers ? 'var(--text)' : 'var(--muted)'}; font-weight: bold;">${esc(layer.name)}</strong>
                            <span class="badge ${badgeClass}" style="padding: ${useNumbers ? '2px 8px' : '1px 6.5px'}; font-size: ${useNumbers ? '12px' : '10.5px'}; display:inline-flex; align-items:center;">${statusPulse}${badgeText}</span>
                        </div>
                        <div style="font-size: ${useNumbers ? '12.5px' : '12px'}; color: ${useNumbers ? 'var(--muted)' : 'var(--text)'}; font-weight: ${useNumbers ? 'normal' : '500'}; line-height: normal; margin-top: 1px; word-break: break-word;" title="${esc(info.details)}">${esc(info.details)}</div>
                        ${errorHtml}
                    </div>
                `;
            }).join("");
        }
