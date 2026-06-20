
        function switchSubSettingTab(subTabName) {
            document.querySelectorAll('[data-subtab]').forEach(btn => {
                const isActive = btn.getAttribute('data-subtab') === subTabName;
                btn.classList.toggle('active', isActive);
                btn.classList.toggle('text-primary', isActive);
                btn.classList.toggle('border-primary', isActive);
                btn.classList.toggle('text-muted', !isActive);
                btn.classList.toggle('border-transparent', !isActive);
            });
            document.querySelectorAll('.subsettings-content').forEach(content => {
                const isActive = content.id === `subsetting-${subTabName}`;
                content.classList.toggle('active', isActive);
                content.classList.toggle('block', isActive);
                content.classList.toggle('hidden', !isActive);
            });

            if (subTabName === "logs") {
                loadLogs();
            } else if (subTabName === "diagnostics") {
                loadGatewayStatus();
                if (window.testLayeredHealth) {
                    window.testLayeredHealth(false);
                }
            }
        }

        function handleRoutingModeChange(mode) {
            const countryGroup = $("net_force_country_group");
            const warningDiv = $("net_routing_warning");

            if (mode === "fixed_region") {
                countryGroup.style.display = "block";
                warningDiv.className = "notice";
                warningDiv.style.borderColor = "rgba(255,149,0,0.2)";
                warningDiv.style.background = "var(--yellow-soft)";
                warningDiv.style.color = "var(--yellow)";
                warningDiv.innerHTML = `<strong>注意</strong>: 固定地区将仅切换和建立与该地区节点的 VPN 连接。若该地区全部节点异常，将可能造成断网。`;
            } else if (mode === "fixed_ip") {
                countryGroup.style.display = "none";
                warningDiv.className = "notice";
                warningDiv.style.borderColor = "rgba(255,149,0,0.2)";
                warningDiv.style.background = "var(--yellow-soft)";
                warningDiv.style.color = "var(--yellow)";
                warningDiv.innerHTML = `<strong>注意</strong>: 固定 IP 策略将锁定当前连接的节点，不进行自动切换。如该节点异常，将可能会导致中断出网服务。`;
            } else {
                countryGroup.style.display = "none";
                warningDiv.className = "notice";
                warningDiv.style.borderColor = "rgba(94,92,230,0.2)";
                warningDiv.style.background = "var(--primary-soft)";
                warningDiv.style.color = "var(--primary)";
                warningDiv.innerHTML = `<strong>提示</strong>: 自动最快路由策略将自动对所有节点进行网络延迟评测，切换至最佳 IP。`;
            }
        }

        function populateRoutingCountries() {
            const select = $("net_force_country");
            if (!select) return;
            const countMap = {};
            nodes.forEach(n => {
                if (n.country) {
                    countMap[n.country] = (countMap[n.country] || 0) + 1;
                }
            });

            const countries = Object.keys(countMap).sort();
            let html = '<option value="">请选择要锁定的国家...</option>';
            countries.forEach(c => {
                html += `<option value="${esc(c)}">${esc(translateCountry(c))} (${countMap[c]}个节点)</option>`;
            });
            select.innerHTML = html;

            if (state) {
                const mode = state.routing_mode || "auto";
                const modeRadio = document.querySelector(`input[name="net_routing_mode"][value="${mode}"]`);
                if (modeRadio) modeRadio.checked = true;
                select.value = state.force_country || "";
                handleRoutingModeChange(mode);
            }
        }

        let currentCredentialUsername = "";

        function populateSettingsForms() {
            if (!state) return;
            const credUsername = $("cred_username");
            const netPort = $("net_port");
            const netSuffix = $("net_suffix");
            const netProxyPort = $("net_proxy_port");
            currentCredentialUsername = state.username || "";
            if (credUsername) credUsername.value = currentCredentialUsername;
            if (netPort) netPort.value = state.port || 8787;
            if (netSuffix) netSuffix.value = state.secret_path || "";
            if (netProxyPort) netProxyPort.value = state.proxy_port || 7928;

            const netDomain = $("net_domain");
            if (netDomain) netDomain.value = "";
            setManualPathsMode(false);

            if (state.domain_certs && state.domain_certs.length > 0) {
                domainCertsList = JSON.parse(JSON.stringify(state.domain_certs));
                const item = domainCertsList[0];
                $("dc_id").value = item.id;
                $("net_domain").value = item.domain || "";
                $("net_cert_path").value = item.tls_cert_file || "";
                $("net_key_path").value = item.tls_key_file || "";
                $("net_cert_content").value = item.tls_cert_content || "";
                $("net_key_content").value = item.tls_key_content || "";

                if (item.tls_cert_content || item.tls_key_content) {
                    toggleDomainCertMode("content");
                } else {
                    toggleDomainCertMode("path");
                    const expectedCert = `/etc/letsencrypt/live/${item.domain}/fullchain.pem`;
                    const expectedKey = `/etc/letsencrypt/live/${item.domain}/privkey.pem`;
                    if (item.tls_cert_file && (item.tls_cert_file !== expectedCert || item.tls_key_file !== expectedKey)) {
                        setManualPathsMode(true);
                    } else {
                        setManualPathsMode(false);
                    }
                }
            } else {
                domainCertsList = [];
                $("dc_id").value = "";
                $("net_domain").value = "";
                $("net_cert_content").value = "";
                $("net_key_content").value = "";
                toggleDomainCertMode("path");
                setManualPathsMode(false);
            }

            populateRoutingCountries();
        }

        function showGlobalToast(message, type = "success") {
            let container = $("toast-container");
            if (!container) {
                container = document.createElement("div");
                container.id = "toast-container";
                document.body.appendChild(container);
            }
            const toast = document.createElement("div");
            toast.className = `toast toast-${type}`;
            toast.textContent = message;

            if (type === "success") {
                toast.style.borderColor = "var(--success)";
                toast.style.color = "var(--success)";
            } else if (type === "danger") {
                toast.style.borderColor = "var(--danger)";
                toast.style.color = "var(--danger)";
            } else if (type === "loading") {
                toast.style.borderColor = "var(--primary)";
                toast.style.color = "var(--primary)";
            }

            container.appendChild(toast);
            setTimeout(() => {
                toast.style.animation = "toastOut 0.3s cubic-bezier(0.4, 0, 0.2, 1) both";
                setTimeout(() => toast.remove(), 300);
            }, 3000);
        }

        let domainCertsList = [];
        let manualCertPaths = false;

        function renderDomainCertsList() {
            // No-op as pool list UI is removed
        }

        function getLetsEncryptPaths(domain) {
            const safeDomain = (domain || "").trim();
            return {
                cert: safeDomain ? `/etc/letsencrypt/live/${safeDomain}/fullchain.pem` : "",
                key: safeDomain ? `/etc/letsencrypt/live/${safeDomain}/privkey.pem` : ""
            };
        }

        function refreshAutoCertPaths() {
            if (manualCertPaths) return;
            const domainInput = $("net_domain");
            const certInput = $("net_cert_path");
            const keyInput = $("net_key_path");
            if (!domainInput || !certInput || !keyInput) return;
            const paths = getLetsEncryptPaths(domainInput.value);
            certInput.value = paths.cert;
            keyInput.value = paths.key;
        }

        function setManualPathsMode(enabled) {
            manualCertPaths = Boolean(enabled);
            const certInput = $("net_cert_path");
            const keyInput = $("net_key_path");
            const toggleBtn = $("btn_toggle_edit_paths");
            if (certInput) {
                certInput.readOnly = !manualCertPaths;
                certInput.style.opacity = manualCertPaths ? "1" : "0.85";
            }
            if (keyInput) {
                keyInput.readOnly = !manualCertPaths;
                keyInput.style.opacity = manualCertPaths ? "1" : "0.85";
            }
            if (toggleBtn) {
                toggleBtn.style.color = manualCertPaths ? "var(--primary)" : "var(--muted)";
                toggleBtn.title = manualCertPaths ? "恢复自动路径" : "手动修改路径";
            }
            refreshAutoCertPaths();
        }

        function toggleManualPaths() {
            setManualPathsMode(!manualCertPaths);
        }

        function toggleDomainCertMode(mode) {
            const pathInputs = $("dc_path_inputs");
            const contentInputs = $("dc_content_inputs");
            const pathRadio = $("dc_mode_path");
            const contentRadio = $("dc_mode_content");

            const useContent = mode === "content";
            if (pathRadio) pathRadio.checked = !useContent;
            if (contentRadio) contentRadio.checked = useContent;
            if (pathInputs) pathInputs.style.display = useContent ? "none" : "grid";
            if (contentInputs) contentInputs.style.display = useContent ? "grid" : "none";
            if (!useContent) refreshAutoCertPaths();
        }

        function clearDomainCertConfig() {
            if (!confirm("确定要清除域名与证书配置吗？")) return;
            domainCertsList = [];
            $("dc_id").value = "";
            $("net_domain").value = "";
            $("net_cert_content").value = "";
            $("net_key_content").value = "";
            toggleDomainCertMode("path");
            setManualPathsMode(false);
            globalSaveSettings(false);
        }

        function addOrUpdateDomainCert(e) {
            e.preventDefault();
            const id = $("dc_id").value;
            const domain = $("net_domain").value.trim();
            const mode = document.querySelector('input[name="dc_mode"]:checked').value;

            let certPath = "";
            let keyPath = "";
            let certContent = "";
            let keyContent = "";

            if (mode === "path") {
                certPath = $("net_cert_path").value.trim();
                keyPath = $("net_key_path").value.trim();
                if (!certPath || !keyPath) {
                    alert("证书路径与密钥路径不能为空");
                    return;
                }
            } else {
                certContent = $("net_cert_content").value.trim();
                keyContent = $("net_key_content").value.trim();
                if (!certContent || !keyContent) {
                    alert("证书明文内容和密钥明文内容必须同时填写");
                    return;
                }
                if (!certContent.includes("BEGIN CERTIFICATE") || !keyContent.includes("BEGIN")) {
                    alert("证书或私钥格式不正确，必须为 PEM 格式明文内容");
                    return;
                }
            }

            if (!domain) {
                alert("域名不能为空");
                return;
            }

            if (domain && !/^[a-zA-Z0-9.-]+$/.test(domain)) {
                alert("域名格式不正确，仅支持英文字母、数字、点(.)和横杠(-)");
                return;
            }

            const newId = id || 'dc-' + Math.random().toString(36).substr(2, 9);
            const item = {
                id: newId,
                domain: domain,
                tls_cert_file: certPath,
                tls_key_file: keyPath,
                tls_cert_content: certContent,
                tls_key_content: keyContent,
                active: true
            };

            domainCertsList = [item];
            globalSaveSettings(false);
        }

        async function globalSaveSettings(restart) {
            const username = $("cred_username").value.trim();
            const password = $("cred_password").value.trim();
            const port = parseInt($("net_port").value);
            const proxyPort = parseInt($("net_proxy_port").value);
            const suffix = $("net_suffix").value.trim();
            const usernameChanged = username && username !== currentCredentialUsername;
            const shouldUpdateCredentials = usernameChanged || password.length > 0;

            if (isNaN(port) || port < 1 || port > 65535) {
                alert("网页管理端口范围必须在 1 至 65535 之间");
                return;
            }
            if (isNaN(proxyPort) || proxyPort < 1024 || proxyPort > 65535) {
                alert("代理出站端口范围必须在 1024 至 65535 之间");
                return;
            }
            if (proxyPort === port) {
                alert("代理出站端口不能与网页管理端口相同");
                return;
            }
            if (!/^[A-Za-z0-9]+$/.test(suffix)) {
                alert("登录安全后缀仅能由英文字母和数字组成");
                return;
            }

            const payload = {
                port: port,
                proxy_port: proxyPort,
                secret_path: suffix,
                domain_certs: domainCertsList
            };

            if (shouldUpdateCredentials) {
                if (!username || !password) {
                    alert("若要修改管理员账号，新账号与新密码必须同时填写");
                    return;
                }
                payload.username = username;
                payload.password = password;
            }

            if (restart) {
                payload.restart_now = true;
            }

            showGlobalToast("正在保存配置，请稍候...", "loading");

            try {
                if (shouldUpdateCredentials) {
                    const credRes = await fetch("./api/update_credentials", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ username, password })
                    });
                    const credData = await credRes.json();
                    if (!credRes.ok || !credData.ok) {
                        showGlobalToast(credData.error || "管理员凭据保存失败", "danger");
                        return;
                    }
                }

                const res = await fetch("./api/update_settings", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });

                const data = await res.json();
                if (res.ok && data.ok) {
                    if (data.restart_needed || restart) {
                        showGlobalToast("保存成功！服务正在重启中，网页即将自动跳转...", "success");
                        document.querySelectorAll("#tab-settings input, #tab-settings button").forEach(el => el.disabled = true);

                        setTimeout(() => {
                            const protocol = window.location.protocol;
                            const host = window.location.hostname;
                            const activeCert = domainCertsList.find(x => x.active);
                            const targetHost = (activeCert && activeCert.domain) ? activeCert.domain : host;
                            window.location.href = `${protocol}//${targetHost}:${port}/${suffix}/`;
                        }, 4000);
                    } else {
                        showGlobalToast("配置保存并应用成功，已即时生效！", "success");
                        $("cred_password").value = "";
                        load();
                    }
                } else {
                    showGlobalToast(data.error || "保存失败，请检查输入", "danger");
                }
            } catch (err) {
                showGlobalToast("连接服务器失败，请稍后重试", "danger");
            }
        }

        let gatewayPollInterval = null;

        function loadGatewayStatus() {
            loadGatewayStatusCall();
            if (gatewayPollInterval) clearInterval(gatewayPollInterval);
            gatewayPollInterval = setInterval(loadGatewayStatusCall, 3000);
        }

        async function loadGatewayStatusCall() {
            // Only fetch when tab is active
            const currentTab = sessionStorage.getItem('currentTab') || 'tab-host';
            if (currentTab !== 'tab-settings' && currentTab !== 'tab-host') {
                if (gatewayPollInterval) {
                    clearInterval(gatewayPollInterval);
                    gatewayPollInterval = null;
                }
                return;
            }
            try {
                const res = await fetch("./api/gateway_status");
                const data = await res.json();
                if (data.ok && data.services) {
                    renderGatewayServices(data.services);
                }
            } catch (e) {
                console.error("加载网关状态失败", e);
            }
        }

        function renderGatewayServices(services) {
            let html = "";
            services.forEach(s => {
                const statusText = s.status === "running" ? "正在运行" : "已停止";
                const badgeClass = s.status === "running" ? "available" : "unavailable";
                const statusPulse = s.status === "running" ? '<span class="badge-pulse"></span>' : '';

                html += `
                    <div class="glass" style="background: var(--control); border-radius: var(--radius); padding: 14px 18px; display: flex; flex-direction: column; gap: 6px;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <strong style="font-size: 14.5px; color: var(--text);">${esc(s.name)}</strong>
                            <span class="badge ${badgeClass}" style="padding: 2px 8px;">${statusPulse}${statusText}</span>
                        </div>
                        <div style="font-size: 12.5px; color: var(--muted);">${esc(s.details || "-")}</div>
                        ${s.error ? `
                            <div style="font-size: 12.5px; color: var(--red); background: var(--red-soft); border: 1px solid rgba(255, 69, 58, 0.15); border-radius: 6px; padding: 8px 12px; margin-top: 4px; line-height: 1.45;">
                                诊断原因: ${esc(s.error)}
                            </div>
                        ` : ''}
                    </div>
                `;
            });
            ["gateway_services_list", "gateway_services_dashboard"].forEach(id => {
                const container = $(id);
                if (container) container.innerHTML = html;
            });
        }

        let logsPollInterval = null;
        let rawLogsCache = [];

        function loadLogs() {
            loadLogsCall();
            if (logsPollInterval) clearInterval(logsPollInterval);
            logsPollInterval = setInterval(loadLogsCall, 2500);
        }

        async function loadLogsCall() {
            const currentTab = sessionStorage.getItem('currentTab') || 'tab-host';
            if (currentTab !== 'tab-settings') {
                if (logsPollInterval) {
                    clearInterval(logsPollInterval);
                    logsPollInterval = null;
                }
                return;
            }
            try {
                const res = await fetch("./api/logs");
                const data = await res.json();
                if (data.logs) {
                    rawLogsCache = data.logs;
                    filterAndRenderLogs();
                }
            } catch (e) {
                console.error("加载日志失败", e);
            }
        }

        function filterAndRenderLogs() {
            const filterVal = $("log_filter_select").value;
            const term = $("log_terminal_container");
            if (!term) return;

            let filtered = rawLogsCache;
            if (filterVal === "proxy") {
                filtered = rawLogsCache.filter(l => l.module === "Proxy");
            } else if (filterVal === "vpn") {
                filtered = rawLogsCache.filter(l => l.module === "VPN");
            } else if (filterVal === "xray") {
                filtered = rawLogsCache.filter(l => l.module === "Xray");
            } else if (filterVal === "system") {
                filtered = rawLogsCache.filter(l => !["Proxy", "VPN", "Xray"].includes(l.module));
            }

            if (filtered.length === 0) {
                term.innerHTML = `<div style="color: var(--muted); text-align: center; margin-top: 150px;">暂无该类型日志。</div>`;
                return;
            }

            const linesHtml = filtered.map(l => {
                let color = "#e5e7eb";
                if (l.module === "Proxy") color = "#38bdf8";
                if (l.module === "VPN") color = "#34d399";
                if (l.module === "Xray") color = "#c084fc";
                if (l.level === "WARNING") color = "#fbbf24";
                if (l.level === "ERROR") color = "#f87171";

                return `<div style="color: ${color}; margin-bottom: 4px;">[${esc(l.timestamp)}] [${esc(l.level)}] [${esc(l.module)}] ${esc(l.message)}</div>`;
            }).join("");

            const isAtBottom = term.scrollHeight - term.clientHeight <= term.scrollTop + 50;
            term.innerHTML = linesHtml;

            if (isAtBottom) {
                term.scrollTop = term.scrollHeight;
            }
        }

        async function copyLogContent() {
            const term = $("log_terminal_container");
            if (!term) return;

            const text = term.innerText || term.textContent;
            if (!text || text.includes("暂无今日") || text.includes("暂无该类型") || text.includes("正在读取")) {
                showGlobalToast("当前没有可供复制的日志。", "danger");
                return;
            }

            try {
                await copyToClipboard(text);
                showGlobalToast("日志内容已成功复制到剪贴板！", "success");
            } catch (err) {
                showGlobalToast("复制失败，请重试", "danger");
            }
        }

        function exportLogContent() {
            const term = $("log_terminal_container");
            if (!term) return;

            const text = term.innerText || term.textContent;
            if (!text || text.includes("暂无今日") || text.includes("暂无该类型") || text.includes("正在读取")) {
                alert("当前没有可供导出的日志。");
                return;
            }

            const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            const dateStr = new Date().toISOString().slice(0, 10);
            const filterVal = $("log_filter_select").value;
            a.download = `vpngate_log_${filterVal}_${dateStr}.txt`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }

        async function clearLogContent() {
            if (!confirm("确定要清除所有运行日志吗？")) return;
            try {
                const res = await fetch("./api/clear_logs", { method: "POST" });
                const data = await res.json();
                if (res.ok && data.ok) {
                    rawLogsCache = [];
                    filterAndRenderLogs();
                    alert(`日志已清除`);
                    loadLogs();
                } else {
                    alert(data.error || "清除日志失败");
                }
            } catch (e) {
                alert("清除日志失败，请检查服务状态。");
            }
        }

        document.addEventListener("DOMContentLoaded", () => {
            const domainInput = $("net_domain");
            if (domainInput) {
                domainInput.addEventListener("input", refreshAutoCertPaths);
            }
        });

        function renderLayeredHealthList(data) {
            const container = $("layered_health_list");
            if (!container) return;
            
            const layers = [
                { key: "api_connectivity", name: "1. API 源通畅度" },
                { key: "node_pool", name: "2. 节点池可用性" },
                { key: "openvpn_interface", name: "3. OpenVPN网卡状态" },
                { key: "policy_routing", name: "4. 策略路由健康度" },
                { key: "local_proxy", name: "5. 本地代理连通性" }
            ];
            
            container.innerHTML = layers.map(layer => {
                const info = data[layer.key] || { ok: false, details: "未检测" };
                const statusText = info.ok ? "正常" : (info.details === "未启用" || info.details === "未启动" || info.details === "未检测" || info.details === "OpenVPN 连接未启动" || info.details === "Xray 代理服务未运行" || info.details.includes("未运行") ? "未就绪" : "异常");
                const badgeClass = info.ok ? "available" : (info.details === "未启用" || info.details === "未启动" || info.details === "未检测" || info.details === "OpenVPN 连接未启动" || info.details === "Xray 代理服务未运行" || info.details.includes("未运行") ? "not_checked" : "unavailable");
                const statusPulse = info.ok ? '<span class="badge-pulse"></span>' : '';
                
                let errorHtml = "";
                if (!info.ok && info.error_type) {
                    let desc = "";
                    if (info.error_type === "PORT_COLLISION") {
                        desc = "诊断原因: 检测到本地代理端口被其他进程占用。请运行 `lsof -i :7928` 查找并结束冲突进程。";
                    } else if (info.error_type === "DNS_POLLUTION") {
                        desc = "诊断原因: 本地 DNS 解析失败或返回了错误的 GFW 污染 IP。建议修改系统 DNS 为 8.8.8.8 等干净的公共解析器，或使用网关内置的 SOCKS5h 远程域名解析。";
                    } else if (info.error_type === "TLS_INTERFERENCE") {
                        desc = "诊断原因: TCP 隧道已接通但 TLS 证书安全握手遭防火墙审查或阻断。说明该节点的 TLS 混淆特征失效，请尝试同步更新到其他公益节点。";
                    } else if (info.error_type === "TUN_DRIVER_MISSING") {
                        desc = "诊断原因: 系统未找到 `/dev/net/tun` 设备。对于 Docker 部署，请确保使用 `--device=/dev/net/tun` 挂载，并拥有 NET_ADMIN 特权；主机环境请使用 `modprobe tun` 加载内核驱动。";
                    } else if (info.error_type === "RP_FILTER_STRICT") {
                        desc = "诊断原因: 内核严格反向路径过滤 rp_filter 被启用，导致 VPN 网卡回包被丢弃。请在主机执行 `sysctl -w net.ipv4.conf.all.rp_filter=2`。";
                    }
                    if (desc) {
                        errorHtml = `
                            <div style="font-size: 12px; color: var(--red); background: var(--red-soft); border: 1px solid rgba(255, 69, 58, 0.15); border-radius: 6px; padding: 8px 12px; margin-top: 6px; line-height: 1.45;">
                                ${esc(desc)}
                            </div>
                        `;
                    }
                }
                
                return `
                    <div class="glass" style="background: var(--control); border-radius: var(--radius); padding: 14px 18px; display: flex; flex-direction: column; gap: 6px;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <strong style="font-size: 14.5px; color: var(--text);">${esc(layer.name)}</strong>
                            <span class="badge ${badgeClass}" style="padding: 2px 8px;">${statusPulse}${statusText}</span>
                        </div>
                        <div style="font-size: 12.5px; color: var(--muted);">${esc(info.details || "-")}</div>
                        ${errorHtml}
                    </div>
                `;
            }).join("");
        }
        
        window.renderLayeredHealthList = renderLayeredHealthList;
