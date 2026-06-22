function toggleVPSModal() {
            const modal = document.getElementById("vps-modal");
            if (modal) {
                const isVisible = modal.style.display === "flex";
                modal.style.display = isVisible ? "none" : "flex";
            }
        }
        function closeVPSModal() {
            const modal = document.getElementById("vps-modal");
            if (modal) modal.style.display = "none";
        }

        function drawTrafficChart(history) {
            const container = $("traffic-chart-container");
            if (!container) return;
            if (!history || history.length === 0) {
                container.innerHTML = '<span style="color: var(--muted); font-size: 13px;">暂无历史流量趋势数据</span>';
                return;
            }

            const width = container.clientWidth || 500;
            const height = 150;
            const paddingLeft = 60;
            const paddingRight = 20;
            const paddingTop = 15;
            const paddingBottom = 25;

            const chartWidth = width - paddingLeft - paddingRight;
            const chartHeight = height - paddingTop - paddingBottom;

            const values = history.map(h => h.bytes || 0);
            const maxVal = Math.max(...values, 1024 * 1024);

            const getX = (index) => paddingLeft + (index / (history.length - 1 || 1)) * chartWidth;
            const getY = (val) => paddingTop + chartHeight - (val / maxVal) * chartHeight;

            function formatChartYLabel(bytes) {
                if (bytes === 0) return '0 B';
                const k = 1024;
                const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
                const i = Math.floor(Math.log(bytes) / Math.log(k));
                return (bytes / Math.pow(k, i)).toFixed(0) + ' ' + sizes[i];
            }

            let svgContent = '';
            svgContent += `
                <defs>
                    <linearGradient id="chartGrad" x1="0%" y1="0%" x2="0%" y2="100%">
                        <stop offset="0%" stop-color="var(--primary)" stop-opacity="0.25" />
                        <stop offset="100%" stop-color="var(--primary)" stop-opacity="0.0" />
                    </linearGradient>
                </defs>
            `;

            for (let i = 0; i <= 3; i++) {
                const gridVal = (maxVal / 3) * i;
                const y = getY(gridVal);
                svgContent += `
                    <line x1="${paddingLeft}" y1="${y}" x2="${width - paddingRight}" y2="${y}" stroke="var(--border)" stroke-width="1" stroke-dasharray="3,3" />
                    <text x="${paddingLeft - 10}" y="${y + 4}" fill="var(--muted)" font-size="10" font-family="sans-serif" text-anchor="end">${formatChartYLabel(gridVal)}</text>
                `;
            }

            const points = history.map((h, idx) => ({ x: getX(idx), y: getY(h.bytes || 0) }));
            const pathD = points.reduce((acc, p, idx) => acc + (idx === 0 ? `M ${p.x} ${p.y}` : ` L ${p.x} ${p.y}`), '');

            let fillPathD = '';
            if (points.length > 0) {
                fillPathD = `${pathD} L ${points[points.length - 1].x} ${paddingTop + chartHeight} L ${points[0].x} ${paddingTop + chartHeight} Z`;
            }

            if (fillPathD) {
                svgContent += `<path d="${fillPathD}" fill="url(#chartGrad)" />`;
            }
            if (pathD) {
                svgContent += `<path d="${pathD}" fill="none" stroke="var(--primary)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />`;
            }

            const step = Math.ceil(history.length / 8);
            history.forEach((h, idx) => {
                const p = points[idx];
                svgContent += `
                    <circle cx="${p.x}" cy="${p.y}" r="3.5" fill="var(--primary)" stroke="var(--panel-strong)" stroke-width="1.5" />
                `;
                if (idx % step === 0 || idx === history.length - 1) {
                    svgContent += `
                        <text x="${p.x}" y="${height - 6}" fill="var(--muted)" font-size="9.5" font-family="sans-serif" text-anchor="middle">${h.hour}</text>
                    `;
                }
            });

            container.innerHTML = `<svg width="100%" height="${height}" style="overflow: visible;">${svgContent}</svg>`;
        }

        async function fetchStats() {
            try {
                const response = await fetch("./api/stats");
                if (!response.ok) return;
                const stats = await response.json();
                const setText = (id, value) => {
                    const el = document.getElementById(id);
                    if (el) el.innerText = value;
                };
                const setRing = (id, value) => {
                    const el = document.getElementById(id);
                    if (!el) return;
                    const pct = Math.max(0, Math.min(100, Number(value) || 0));
                    el.style.setProperty("--pct", pct);
                };

                setText('cpu-val', `${stats.cpu_percent}%`);
                setRing('cpu-ring', stats.cpu_percent);
                setText('cpu-detail', `${stats.cpu_cores || 1} 核心，${stats.cpu_percent < 70 ? '负载正常' : stats.cpu_percent < 90 ? '负载较高' : '负载高'}`);

                setText('ram-val', `${stats.memory_percent}%`);
                setRing('ram-ring', stats.memory_percent);
                setText('ram-detail', `${stats.memory_used_gb} / ${stats.memory_total_gb} GB`);

                setText('disk-val', `${stats.disk_percent}%`);
                setRing('disk-ring', stats.disk_percent);
                setText('disk-detail', `${stats.disk_used_gb} / ${stats.disk_total_gb} GB`);

                let uptime_str = "未知";
                if (stats.uptime_seconds > 0) {
                    const days = Math.floor(stats.uptime_seconds / (3600 * 24));
                    const hours = Math.floor((stats.uptime_seconds % (3600 * 24)) / 3600);
                    const minutes = Math.floor((stats.uptime_seconds % 3600) / 60);
                    uptime_str = `${days}天 ${hours}小时 ${minutes}分钟`;
                }
                setText('sys-uptime', uptime_str);
                const openvpnStatusText = stats.ovpn_status === 'active'
                    ? "已连接"
                    : (!isFeatureEnabled("vpngate_enabled") ? "功能未开启" : (state.openvpn_enabled ? "已启用，等待连接" : "未启动"));
                setText('sys-connections', openvpnStatusText);
                setText('sys-proxy-port', state.proxy_port || 7928);
                setText('vpngate_openvpn_text', openvpnStatusText);
                const activeNode = Array.isArray(nodes) ? nodes.find(item => item.id === state.active_openvpn_node_id) : null;
                const activeNodeLabel = activeNode
                    ? `${translateCountry(activeNode.country)} ${activeNode.ip || activeNode.remote_host || ""}`
                    : (state.active_openvpn_node_id || "未连接");
                setText('vpngate_node_text', activeNodeLabel);
                const proxyText = state.proxy_ok
                    ? `${state.proxy_ip || "-"} / ${state.proxy_latency_ms || 0} ms`
                    : (state.proxy_error || "未检测");
                setText('vpngate_proxy_text', proxyText);

                if (stats.traffic) {
                    const tx = stats.traffic.session_tx || 0;
                    const rx = stats.traffic.session_rx || 0;
                    setText('traffic-session', `${formatBytes(tx)} / ${formatBytes(rx)}`);
                    setText('traffic-cycle', formatBytes(stats.traffic.cycle_total || 0));
                    setText('traffic-cumulative', formatBytes(stats.traffic.cumulative_total || 0));
                }

                if (stats.client_traffic) {
                    for (const [name, cStats] of Object.entries(stats.client_traffic)) {
                        const upEl = document.getElementById(`traffic-up-${name}`);
                        const downEl = document.getElementById(`traffic-down-${name}`);
                        const uploaded = cStats.uploaded || 0;
                        const downloaded = cStats.downloaded || 0;
                        if (upEl) upEl.innerText = `↑ ${formatBytes(uploaded)}`;
                        if (downEl) downEl.innerText = `↓ ${formatBytes(downloaded)}`;

                        const row = document.querySelector(`.client-row[data-client-name="${name}"]`);
                        if (row) {
                            const upInput = row.querySelector(".client-uploaded");
                            const downInput = row.querySelector(".client-downloaded");
                            if (upInput) upInput.value = uploaded;
                            if (downInput) downInput.value = downloaded;
                        }
                    }
                }

                stats_cache = stats;
                if (stats.traffic_history) {
                    drawTrafficChart(stats.traffic_history);
                }
            } catch (error) {
                console.error("Stats fetch error:", error);
            }
        }

        function proxyStatusPanelHtml(disabled = false) {
            return `
                <div class="proxy-status-panel">
                    <div class="proxy-status-main">
                        <div class="proxy-status-title">本地代理出口</div>
                        <div class="proxy-status-line">
                            <span id="proxy_status_badge" class="badge not_checked">未检测</span>
                            <span>IP: <strong id="proxy_ip_val">--</strong></span>
                            <span id="proxy_latency_val"></span>
                        </div>
                    </div>
                    <div class="proxy-status-actions">
                        <button id="btn_test_proxy" class="btn btn-secondary btn-sm proxy-check-btn" onclick="testLocalProxy()" ${disabled ? "disabled" : ""}>检测</button>
                    </div>
                </div>
            `;
        }

        let pollInterval = null;

        function startConnectionPolling() {
            if (pollInterval) clearInterval(pollInterval);
            pollInterval = setInterval(async () => {
                try {
                    const resp = await fetch("./api/nodes");
                    const data = await resp.json();
                    nodes = data.nodes || [];
                    state = data.state || {};
                    stableSortNodes();
                    render();
                    fetchStats();

                    if (!state.is_connecting) {
                        clearInterval(pollInterval);
                        pollInterval = null;
                        try {
                            await fetch("./api/test_proxy", { method: "POST" });
                        } catch (pe) { }
                        load();
                    }
                } catch (pe) {
                    clearInterval(pollInterval);
                    pollInterval = null;
                    load();
                }
            }, 1000);
        }

        async function startOpenvpnService() {
            if (!isFeatureEnabled("vpngate_enabled")) {
                showToast("请先开启 VPNGate 公益节点功能", "warning");
                return;
            }
            state.openvpn_enabled = true;
            state.is_connecting = true;
            state.active_node_latency = "正在准备";
            state.last_check_message = "正在启动 OpenVPN 并选择可用节点...";
            render();

            startConnectionPolling();

            try {
                const r = await fetch("./api/openvpn/start", { method: "POST" });
                const result = await r.json();
                if (!result.ok) {
                    alert("启动 OpenVPN 失败: " + (result.error || "未知错误"));
                    if (pollInterval) {
                        clearInterval(pollInterval);
                        pollInterval = null;
                    }
                    state.openvpn_enabled = false;
                    state.is_connecting = false;
                    render();
                    return;
                }
            } catch (e) {
                alert("启动 OpenVPN 请求失败");
                if (pollInterval) {
                    clearInterval(pollInterval);
                    pollInterval = null;
                }
                state.openvpn_enabled = false;
                state.is_connecting = false;
                render();
            }
        }

        async function disconnectNode() {
            if (!confirm("确定要停止 OpenVPN 吗？Xray 会继续保持运行。")) return;
            try {
                const response = await fetch("./api/openvpn/stop", { method: "POST" });
                const result = await response.json();
                if (result.ok) {
                    try {
                        await fetch("./api/test_proxy", { method: "POST" });
                    } catch (pe) { }
                    load();
                } else {
                    alert("断开连接失败: " + (result.error || "未知错误"));
                }
            } catch (e) {
                alert("请求断开连接失败");
            }
        }

        async function testLocalProxy() {
            const btn = $("btn_test_proxy");
            const badge = $("proxy_status_badge");
            const ipVal = $("proxy_ip_val");
            const latVal = $("proxy_latency_val");
            if (!btn || !badge || !ipVal || !latVal) return;

            btn.disabled = true;
            btn.innerHTML = `<span class="badge-pulse"></span>测试中...`;
            badge.className = "badge not_checked";
            badge.textContent = "检测中...";
            ipVal.textContent = "-";
            latVal.textContent = "";

            try {
                const response = await fetch("./api/test_proxy", { method: "POST" });
                const result = await response.json();
                if (result.ok) {
                    badge.className = "badge available";
                    badge.textContent = "可用";
                    ipVal.textContent = result.ip || "-";

                    const latencyClass = getLatencyClass(result.latency_ms);
                    latVal.innerHTML = `<span class="latency-val ${latencyClass}">${result.latency_ms} ms</span>`;
                } else {
                    badge.className = "badge unavailable";
                    badge.textContent = "不可用";
                    ipVal.textContent = "-";
                    latVal.innerHTML = `<span class="latency-val latency-poor" style="font-size:11px;" title="${esc(result.error)}">连接失败</span>`;
                }
            } catch (e) {
                badge.className = "badge unavailable";
                badge.textContent = "网络错误";
                ipVal.textContent = "-";
                latVal.innerHTML = `<span class="latency-val latency-poor" style="font-size:11px;">请求出错</span>`;
            } finally {
                btn.disabled = false;
                btn.innerHTML = `检测`;
            }
        }

        function renderXrayStatus(status) {
            if (!status) return;
            $("xray_installed_text").textContent = status.installed ? "已安装" : "未安装";
            $("xray_running_text").textContent = status.running ? "运行中" : "未运行";
            $("xray_enabled_text").textContent = status.enabled ? "已启用" : "未启用";
            const errBox = $("xray_error_box");
            if (status.last_error) {
                errBox.textContent = status.last_error;
                errBox.style.display = "block";
            } else {
                errBox.style.display = "none";
            }
            $("xray_start_btn").disabled = !status.installed || status.running;
            $("xray_stop_btn").disabled = !status.running;
            $("xray_restart_btn").disabled = !status.installed;
            const installBtn = $("xray_install_btn");
            if (installBtn) installBtn.disabled = status.installed;
        }

        async function refreshXrayStatus() {
            try {
                const res = await fetch("./api/xray/status");
                const status = await res.json();
                renderXrayStatus(status);
            } catch (e) {
                const errBox = $("xray_error_box");
                errBox.textContent = "读取 Xray 状态失败，请检查管理服务是否正常。";
                errBox.style.display = "block";
            }
        }

        async function xrayAction(action) {
            try {
                const res = await fetch("./api/xray/action", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ action })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    alert(data.error || "Xray 操作失败，请查看日志。");
                }
                await loadXrayPanel();
                fetchStats();
            } catch (e) {
                alert("Xray 操作请求失败。");
            }
        }

        async function testLayeredHealth(force = false) {
            const container = $("layered_health_dashboard");
            if (!container) return;
            
            const btn = $("btn_refresh_layered_health");
            if (force && btn) {
                btn.disabled = true;
                btn.classList.add("is-loading");
                btn.innerHTML = `<i data-lucide="refresh-cw" class="btn-icon" aria-hidden="true"></i>自检中`;
            }
            
            try {
                const res = await fetch("./api/layered_health", { method: "POST" });
                const data = await res.json();
                renderLayeredHealthDashboard(data);
                if (window.renderLayeredHealthList) {
                    window.renderLayeredHealthList(data);
                }
            } catch (e) {
                container.innerHTML = `<div class="col-span-5 text-center text-red-500 py-6 text-sm">自检接口错误: ${esc(e.message || e)}</div>`;
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.classList.remove("is-loading");
                    btn.innerHTML = `<i data-lucide="refresh-cw" class="btn-icon" aria-hidden="true"></i>一键自检`;
                }
            }
        }

        function renderLayeredHealthDashboard(data) {
            const container = $("layered_health_dashboard");
            if (!container) return;
            
            const layers = [
                { key: "api_connectivity", name: "API 源通畅度" },
                { key: "node_pool", name: "节点池可用性" },
                { key: "openvpn_interface", name: "OpenVPN网卡" },
                { key: "policy_routing", name: "策略路由" },
                { key: "local_proxy", name: "本地代理出口" }
            ];
            
            container.innerHTML = layers.map(layer => {
                const info = data[layer.key] || { ok: false, details: "未检测" };
                let badgeClass = "unavailable";
                let badgeText = "异常";
                
                if (info.ok) {
                    badgeClass = "available";
                    badgeText = "正常";
                } else if (info.details === "未启用" || info.details === "未启动" || info.details === "未检测" || info.details === "OpenVPN 连接未启动" || info.details === "Xray 代理服务未运行" || info.details.includes("未运行")) {
                    badgeClass = "not_checked";
                    badgeText = "未就绪";
                } else if (info.details.includes("免检") || info.details.includes("跳过") || info.details.includes("无需配置")) {
                    badgeClass = "not_checked";
                    badgeText = "跳过";
                }
                
                const statusPulse = info.ok ? '<span class="badge-pulse"></span>' : '';
                
                return `
                    <div class="glass p-4 rounded-[20px] flex flex-col gap-2 border border-border" style="background: var(--control);">
                        <div class="flex justify-between items-center">
                            <span class="text-xs text-muted font-bold">${esc(layer.name)}</span>
                            <span class="badge ${badgeClass}" style="padding: 1px 6.5px; font-size:10.5px; display:inline-flex; align-items:center;">${statusPulse}${badgeText}</span>
                        </div>
                        <p class="text-[12px] text-text font-medium leading-normal mt-1 break-words" title="${esc(info.details)}">
                            ${esc(info.details)}
                        </p>
                    </div>
                `;
            }).join("");
        }

        window.testLayeredHealth = testLayeredHealth;
        window.renderLayeredHealthDashboard = renderLayeredHealthDashboard;
        window.startConnectionPolling = startConnectionPolling;
        window.startOpenvpnService = startOpenvpnService;
        window.disconnectNode = disconnectNode;
        window.testLocalProxy = testLocalProxy;
        window.fetchStats = fetchStats;
        window.drawTrafficChart = drawTrafficChart;
        window.proxyStatusPanelHtml = proxyStatusPanelHtml;
        
        let _first_health_done = false;
        const _orig_fetchStats = fetchStats;
        fetchStats = async function() {
            await _orig_fetchStats();
            if (!_first_health_done) {
                _first_health_done = true;
                testLayeredHealth(false);
            }
        };
