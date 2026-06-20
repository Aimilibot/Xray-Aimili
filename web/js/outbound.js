
        let outboundNodes = [];
        const virtualOutboundNodes = [
            { id: "vpngate-openvpn-active", name: "VPNGate 当前 OpenVPN", type: "vpngate-openvpn" }
        ];
        const outboundTypeNames = {
            "vpngate-openvpn": "VPNGate",
            "warp": "WARP",
            "custom-node": "节点链接",
            "subscription": "订阅链接",
            "json-config": "JSON 配置"
        };

        function browserRegionCode() {
            const locale = navigator.language || "en-US";
            const region = (locale.split("-")[1] || locale.split("_")[1] || "US").toUpperCase();
            return /^[A-Z]{2}$/.test(region) ? region : "US";
        }

        function browserCityName() {
            const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
            const city = (zone.split("/").pop() || "Local").replace(/_/g, "");
            return city || "Local";
        }

        function nextNodeName(prefix) {
            const region = browserRegionCode();
            const city = browserCityName();
            const existingNames = new Set([
                ...outboundNodes.map(item => item.name),
                ...(typeof subscriptionNodes !== "undefined" ? subscriptionNodes.map(item => item.name) : [])
            ].filter(Boolean));
            for (let i = 1; i < 100; i++) {
                const name = `${prefix}-${region}-${city}-${String(i).padStart(2, "0")}`;
                if (!existingNames.has(name)) return name;
            }
            return `${prefix}-${region}-${city}-${Date.now().toString().slice(-4)}`;
        }

        function showOutboundNodeTab(tabName) {
            const vpngatePanel = $("outbound-vpngate-panel");
            const warpPanel = $("outbound-warp-panel");
            const customPanel = $("outbound-custom-panel");
            if (vpngatePanel) vpngatePanel.style.display = tabName === "vpngate" ? "block" : "none";
            if (warpPanel) warpPanel.style.display = tabName === "warp" ? "block" : "none";
            if (customPanel) customPanel.style.display = tabName === "custom" ? "block" : "none";
            if (tabName === "custom") loadOutboundNodes();
            if (tabName === "warp") loadWarpState();
            if (tabName === "vpngate") render();
        }

        let currentWarpNode = null;

        function renderWarpPowerButton() {
            const btn = document.querySelector(`[data-feature-power="warp_enabled"]`);
            if (!btn) return;
            const isOn = !!currentWarpNode && isFeatureEnabled("warp_enabled");
            const resetBtn = $("warp_reset_btn");
            const testBtn = $("warp_test_btn");
            btn.classList.toggle("is-on", isOn);
            btn.setAttribute("aria-pressed", isOn ? "true" : "false");
            btn.title = isOn ? "关闭 WARP" : "启动 WARP";
            const label = btn.querySelector(".feature-power-label");
            if (label) label.textContent = isOn ? "关闭" : "启动";
            if (resetBtn) resetBtn.style.display = isOn ? "" : "none";
            if (testBtn) testBtn.style.display = isOn ? "" : "none";
        }

        async function loadWarpState() {
            if (!isFeatureEnabled("warp_enabled")) {
                currentWarpNode = null;
                const emptyState = $("warp_empty_state");
                const detailsState = $("warp_details_state");
                if (emptyState) {
                    emptyState.style.display = "block";
                    emptyState.innerHTML = featureDisabledHtml("Cloudflare WARP 未启动", "点击右上角启动后会自动创建并测试 WARP 出站。", "warp_enabled");
                }
                if (detailsState) detailsState.style.display = "none";
                renderWarpPowerButton();
                return;
            }
            try {
                const res = await fetch("./api/panel/outbound-nodes");
                const data = await res.json();
                const nodes = Array.isArray(data.nodes) ? data.nodes : [];
                currentWarpNode = nodes.find(item => item.type === "warp");

                const emptyState = $("warp_empty_state");
                const detailsState = $("warp_details_state");

                if (currentWarpNode) {
                    emptyState.style.display = "none";
                    detailsState.style.display = "block";
                    toggleEditWarpEndpoint(false);

                    $("warp_ipv4_val").textContent = (currentWarpNode.addresses || []).find(ip => !ip.includes(":")) || "无";
                    $("warp_ipv6_val").textContent = (currentWarpNode.addresses || []).find(ip => ip.includes(":")) || "无";
                    $("warp_endpoint_val").textContent = currentWarpNode.endpoint || "engage.cloudflareclient.com:2408";
                    $("warp_account_val").textContent = currentWarpNode.account_id || "--";

                    const enabled = currentWarpNode.enabled !== false;
                    const statusVal = $("warp_status_val");
                    statusVal.className = "detail-value status-badge " + (enabled ? "active" : "inactive");
                    statusVal.innerHTML = `<span class="status-dot"></span>${enabled ? "已启动" : "未启动"}`;
                } else {
                    emptyState.style.display = "block";
                    emptyState.textContent = "WARP 尚未创建。点击右上角启动后会自动注册并测试。";
                    detailsState.style.display = "none";
                }
                renderWarpPowerButton();
            } catch (e) {
                console.error("加载 WARP 状态失败", e);
                renderWarpPowerButton();
            }
        }

        async function registerWarpNode(options = {}) {
            if (!isFeatureEnabled("warp_enabled")) {
                showToast("请先开启 Cloudflare WARP 功能", "warning");
                return;
            }
            const isRebuild = !!currentWarpNode;
            if (isRebuild && !options.skipConfirm && !confirm("确定要重建 WARP 配置吗？现有设备密钥将被替换。")) {
                return;
            }

            const powerBtn = document.querySelector(`[data-feature-power="warp_enabled"]`);
            const label = powerBtn ? powerBtn.querySelector(".feature-power-label") : null;
            const originalLabel = label ? label.textContent : "";

            if (powerBtn) powerBtn.disabled = true;
            if (label) label.textContent = "启动中";

            try {
                const res = await fetch("./api/panel/outbound-nodes/warp/register", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" }
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    showToast(data.error || "注册 WARP 出站失败", "error");
                    return;
                }
                showToast(data.message || "WARP 注册成功", "success");
                await loadWarpState();
                if (options.autoTest !== false) {
                    await testWarpNode();
                }
            } catch (e) {
                showToast("无法连接后端注册接口: " + e, "error");
            } finally {
                if (powerBtn) powerBtn.disabled = false;
                if (label) label.textContent = originalLabel || (isFeatureEnabled("warp_enabled") ? "关闭" : "启动");
                renderFeatureGateSwitches();
                renderWarpPowerButton();
            }
        }

        async function toggleWarpFeaturePower() {
            if (currentWarpNode && isFeatureEnabled("warp_enabled")) {
                await setFeatureGate("warp_enabled", false);
                currentWarpNode = null;
                await loadWarpState();
                return;
            }
            if (!isFeatureEnabled("warp_enabled")) {
                await setFeatureGate("warp_enabled", true);
            }
            await loadWarpState();
            if (!currentWarpNode) {
                await registerWarpNode({ skipConfirm: true, autoTest: true });
            }
        }

        async function testWarpNode() {
            if (!isFeatureEnabled("warp_enabled")) {
                showToast("请先开启 Cloudflare WARP 功能", "warning");
                return;
            }
            const btnTest = $("btn_test_warp");
            const testIpVal = $("warp_test_ip_val");
            if (btnTest) btnTest.disabled = true;
            if (testIpVal) {
                testIpVal.textContent = "测试中...";
                testIpVal.style.color = "var(--muted)";
            }

            try {
                const res = await fetch("./api/panel/outbound-nodes/warp/test", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" }
                });
                const data = await res.json();
                if (res.ok && data.ok) {
                    if (testIpVal) {
                        testIpVal.textContent = `${data.ip} (${data.location}) - 延迟: ${data.latency_ms}ms`;
                        testIpVal.style.color = "var(--second)";
                    }
                } else {
                    if (testIpVal) {
                        testIpVal.textContent = "测试失败: " + (data.error || "未知原因");
                        testIpVal.style.color = "var(--warning)";
                    }
                }
            } catch (e) {
                if (testIpVal) {
                    testIpVal.textContent = "连接测试接口失败: " + e;
                    testIpVal.style.color = "var(--warning)";
                }
            } finally {
                if (btnTest) btnTest.disabled = false;
            }
        }

        async function refreshWarpNode() {
            if (!isFeatureEnabled("warp_enabled")) {
                showToast("请先开启 Cloudflare WARP 功能", "warning");
                return;
            }
            if (currentWarpNode && !confirm("确定刷新 WARP 配置吗？现有设备密钥会被替换。")) return;
            try {
                const res = await fetch("./api/panel/outbound-nodes/warp/refresh", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" }
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    showToast(data.error || "刷新 WARP 失败", "error");
                    return;
                }
                showToast(data.message || "WARP 已刷新", "success");
                await loadWarpState();
                await testWarpNode();
            } catch (e) {
                showToast("无法连接 WARP 刷新接口: " + e, "error");
            }
        }

        async function deleteWarpNode() {
            if (!currentWarpNode && !isFeatureEnabled("warp_enabled")) {
                showToast("WARP 当前没有可删除的配置", "warning");
                return;
            }
            if (!confirm("确定删除 WARP 配置并关闭 WARP 功能吗？")) return;
            try {
                await setFeatureGate("warp_enabled", false);
                const res = await fetch("./api/panel/outbound-nodes/warp/delete", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" }
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    showToast(data.error || "删除 WARP 失败", "error");
                    return;
                }
                currentWarpNode = null;
                showToast(data.message || "WARP 已删除", "success");
                await loadWarpState();
                await loadOutboundNodes();
                if (typeof loadRoutingRules === "function") await loadRoutingRules();
            } catch (e) {
                showToast("无法连接 WARP 删除接口: " + e, "error");
            }
        }

        function toggleEditWarpEndpoint(show) {
            const row = $("warp_endpoint_row");
            const editRow = $("warp_endpoint_edit_row");
            if (row) row.style.display = show ? "none" : "flex";
            if (editRow) editRow.style.display = show ? "flex" : "none";
            if (show && currentWarpNode) {
                $("warp_endpoint_input").value = currentWarpNode.endpoint || "engage.cloudflareclient.com:2408";
            }
        }

        async function saveWarpEndpoint() {
            const inputVal = $("warp_endpoint_input").value.trim();
            if (!inputVal) {
                showToast("Endpoint 不能为空", "warning");
                return;
            }
            if (!inputVal.includes(":")) {
                showToast("格式不正确，必须为 host:port 格式", "warning");
                return;
            }
            try {
                const res = await fetch("./api/panel/outbound-nodes/warp/update-endpoint", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ endpoint: inputVal })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    showToast(data.error || "保存 Endpoint 失败", "error");
                    return;
                }
                showToast(data.message || "Endpoint 已保存并应用", "success");
                toggleEditWarpEndpoint(false);
                await loadWarpState();
            } catch (e) {
                showToast("连接接口失败: " + e, "error");
            }
        }

        async function loadOutboundNodes() {
            const disabledState = $("custom_disabled_state");
            const table = $("custom_outbound_table");
            if (!isFeatureEnabled("custom_enabled")) {
                outboundNodes = outboundNodes.filter(item => item.type === "warp");
                if (disabledState) {
                    disabledState.style.display = "block";
                    disabledState.innerHTML = featureDisabledHtml("自定义节点未启动", "点击右上角启动后会加载节点解析和 JSON 出站配置。", "custom_enabled");
                }
                if (table) table.style.display = "none";
                return;
            }
            if (disabledState) {
                disabledState.style.display = "none";
                disabledState.innerHTML = "";
            }
            if (table) table.style.display = "table";
            try {
                const res = await fetch("./api/panel/outbound-nodes");
                const data = await res.json();
                outboundNodes = Array.isArray(data.nodes) ? data.nodes : [];
                renderCustomOutboundNodes();
            } catch (e) {
                const tbody = $("custom_outbound_rows");
                if (tbody) tbody.innerHTML = `<tr><td colspan="5" class="compact-empty">自定义节点加载失败</td></tr>`;
            }
        }

        function renderCustomOutboundNodes() {
            const tbody = $("custom_outbound_rows");
            if (!tbody) return;
            const disabledState = $("custom_disabled_state");
            const table = $("custom_outbound_table");
            if (disabledState) disabledState.style.display = "none";
            if (table) table.style.display = "table";
            const nodes = outboundNodes.filter(item => item.type === "custom-node" || item.type === "subscription" || item.type === "json-config");
            if (!nodes.length) {
                tbody.innerHTML = `<tr><td colspan="5" class="compact-empty">暂无自定义节点</td></tr>`;
                return;
            }
            tbody.innerHTML = nodes.map(node => {
                const enabled = node.enabled !== false;
                const source = node.type === "subscription"
                    ? (node.subscription_url || "-")
                    : node.type === "json-config"
                        ? "JSON 配置"
                        : (node.share_link || [node.host, node.port].filter(Boolean).join(":") || "-");
                return `
                    <tr>
                        <td><span class="inline-flex items-center justify-center rounded-lg py-1 px-2 text-[11px] font-bold text-primary bg-[color-mix(in_srgb,var(--primary)_12%,transparent)] border border-[color-mix(in_srgb,var(--primary)_22%,transparent)]">${esc(outboundTypeNames[node.type] || node.type || "-")}</span></td>
                        <td><span class="status-badge ${enabled ? "active" : "inactive"}" style="display:inline-flex;"><span class="status-dot"></span>${enabled ? "已启用" : "已停用"}</span></td>
                        <td class="min-w-[180px] whitespace-normal py-3 px-3.5">
                            <strong>${esc(node.name || "-")}</strong>
                            <div style="font-size:12px; color:var(--muted); margin-top:3px;">${esc(node.status_text || "已保存，未写入 Xray")}</div>
                        </td>
                        <td>
                            <div>${esc(node.protocol || outboundTypeNames[node.type] || "-")}</div>
                            <div class="text-xs text-muted max-w-[360px] overflow-hidden text-ellipsis whitespace-nowrap" title="${esc(source)}">${esc(source)}</div>
                        </td>
                        <td>
                            <div class="flex gap-2 justify-end flex-wrap">
                                <button type="button" class="min-h-[30px] py-1 px-3 text-[11.5px] w-auto rounded-xl bg-glass border border-border text-text shadow-none hover:bg-glass-strong hover:shadow-none inline-flex justify-center items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed" onclick="${esc(`testOutboundNode(${jsArg(node.id)})`)}" style="width:auto;">测试</button>
                                <button type="button" class="min-h-[30px] py-1 px-3 text-[11.5px] w-auto rounded-xl bg-glass border border-border text-text shadow-none hover:bg-glass-strong hover:shadow-none inline-flex justify-center items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed" onclick="${esc(`editOutboundNode(${jsArg(node.id)})`)}" style="width:auto;">编辑</button>
                                <button type="button" class="min-h-[30px] py-1 px-3 text-[11.5px] w-auto rounded-xl bg-gradient-to-br from-[#ff5370] to-danger text-white border-none shadow-[0_12px_24px_rgba(255,83,112,0.24)] transition-all duration-[280ms] ease-in-out inline-flex justify-center items-center gap-2 hover:translate-y-[-2px] hover:shadow-[0_14px_28px_rgba(255,83,112,0.32)] active:translate-y-0 disabled:opacity-60 disabled:cursor-not-allowed" onclick="${esc(`deleteOutboundNode(${jsArg(node.id)})`)}" style="width:auto;">删除</button>
                            </div>
                        </td>
                    </tr>
                `;
            }).join("");
        }

        async function testOutboundNode(nodeId) {
            if (!isFeatureEnabled("custom_enabled")) {
                showToast("请先开启自定义节点功能", "warning");
                return;
            }
            const node = outboundNodes.find(item => item.id === nodeId);
            const nodeName = node ? (node.name || node.id) : nodeId;
            showToast(`正在测试 ${nodeName}...`, "info");
            try {
                const res = await fetch("./api/panel/outbound-nodes/test", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ id: nodeId })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    showToast(data.error || "自定义节点测试失败", "error");
                    return;
                }
                showToast(`测试成功：${data.ip || "-"} ${data.location || ""}，${data.latency_ms || 0}ms`, "success");
            } catch (e) {
                showToast("连接测试接口失败: " + e, "error");
            }
        }

        async function testAllCustomOutboundNodes() {
            if (!isFeatureEnabled("custom_enabled")) {
                showToast("请先开启自定义节点功能", "warning");
                return;
            }
            const targets = outboundNodes.filter(item =>
                item.enabled !== false &&
                (item.type === "custom-node" || item.type === "subscription" || item.type === "json-config")
            );
            if (!targets.length) {
                showToast("当前没有可测试的自定义节点", "warning");
                return;
            }
            showToast(`开始测试 ${targets.length} 个自定义节点...`, "info");
            let okCount = 0;
            for (const node of targets) {
                try {
                    const res = await fetch("./api/panel/outbound-nodes/test", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ id: node.id })
                    });
                    const data = await res.json();
                    if (res.ok && data.ok) okCount += 1;
                } catch (e) {}
            }
            showToast(`自定义节点测试完成：${okCount}/${targets.length} 可用`, okCount ? "success" : "warning");
        }

        function openOutboundNodeModal(nodeId = "", mode = "add") {
            if (!isFeatureEnabled("custom_enabled")) {
                showToast("请先开启自定义节点功能", "warning");
                return;
            }
            const node = outboundNodes.find(item => item.id === nodeId);
            $("outbound_node_id").value = node ? node.id : "";
            $("outbound_node_created_at").value = node ? (node.created_at || "") : "";
            $("outbound_node_name").value = node ? (node.name || "") : nextNodeName("NODE");
            $("outbound_node_type").value = node ? (node.type || "json-config") : "json-config";
            $("outbound_node_protocol").value = node ? (node.protocol || "") : "";
            $("outbound_node_share_link").value = node ? (node.share_link || "") : "";
            $("outbound_node_subscription_url").value = node ? (node.subscription_url || "") : "";
            $("outbound_node_input_source").value = "";
            $("outbound_node_json_config").value = node ? (node.json_config || "") : "";
            $("outbound_node_error").style.display = "none";
            $("outbound_node_success").style.display = "none";
            const modal = $("outbound-node-modal");
            if (modal) modal.style.display = "flex";
            setTimeout(() => {
                const target = mode === "import" ? $("outbound_node_input_source") : $("outbound_node_json_config");
                if (target) target.focus();
            }, 40);
        }

        function openOutboundAddModal() {
            openOutboundNodeModal("", "add");
        }

        function openOutboundImportModal() {
            openOutboundNodeModal("", "import");
        }

        function closeOutboundNodeModal() {
            const modal = $("outbound-node-modal");
            if (modal) modal.style.display = "none";
        }

        function editOutboundNode(nodeId) {
            openOutboundNodeModal(nodeId);
        }

        async function fetchAndConvertOutbound() {
            if (!isFeatureEnabled("custom_enabled")) {
                showToast("请先开启自定义节点功能", "warning");
                return;
            }
            const sourceVal = $("outbound_node_input_source").value.trim();
            const err = $("outbound_node_error");
            const ok = $("outbound_node_success");
            const btn = $("btn_fetch_convert");
            err.style.display = "none";
            ok.style.display = "none";

            if (!sourceVal) {
                alert("请输入订阅节点或订阅链接");
                return;
            }

            btn.disabled = true;
            btn.textContent = "获取中...";
            try {
                const res = await fetch("./api/panel/outbound-nodes/parse-import", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ input: sourceVal })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    err.textContent = data.error || "获取/解析节点配置失败";
                    err.style.display = "block";
                    return;
                }

                if (data.name && !$("outbound_node_name").value.trim()) {
                    $("outbound_node_name").value = data.name;
                }
                $("outbound_node_type").value = data.type || "custom-node";
                $("outbound_node_protocol").value = data.protocol || "";
                $("outbound_node_share_link").value = data.share_link || "";
                $("outbound_node_subscription_url").value = data.subscription_url || "";
                $("outbound_node_json_config").value = data.json_config;
                ok.textContent = data.type === "subscription" ? "订阅链接已读取，并导入第一个可用节点配置。" : "节点已成功解析并转换为 JSON 配置！";
                ok.style.display = "block";
            } catch (e) {
                err.textContent = "连接解析接口失败: " + e;
                err.style.display = "block";
            } finally {
                btn.disabled = false;
                btn.textContent = "获取";
            }
        }

        async function saveOutboundNode(event) {
            event.preventDefault();
            if (!isFeatureEnabled("custom_enabled")) {
                showToast("请先开启自定义节点功能", "warning");
                return;
            }
            const err = $("outbound_node_error");
            const ok = $("outbound_node_success");
            const btn = $("outbound_node_submit");
            err.style.display = "none";
            ok.style.display = "none";
            const jsonVal = $("outbound_node_json_config").value.trim();
            if (!jsonVal) {
                err.textContent = "JSON 配置不能为空";
                err.style.display = "block";
                return;
            }
            try {
                JSON.parse(jsonVal);
            } catch (e) {
                err.textContent = "JSON 格式不正确: " + e.message;
                err.style.display = "block";
                return;
            }
            const existing = outboundNodes.find(item => item.id === $("outbound_node_id").value);
            const payload = {
                id: $("outbound_node_id").value,
                created_at: $("outbound_node_created_at").value,
                name: $("outbound_node_name").value.trim(),
                type: $("outbound_node_type").value || "json-config",
                protocol: $("outbound_node_protocol").value,
                share_link: $("outbound_node_share_link").value,
                subscription_url: $("outbound_node_subscription_url").value,
                json_config: jsonVal,
                enabled: existing ? existing.enabled !== false : true
            };
            btn.disabled = true;
            btn.textContent = "添加中...";
            try {
                const res = await fetch("./api/panel/outbound-nodes", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    err.textContent = data.error || "保存自定义节点失败";
                    err.style.display = "block";
                    return;
                }
                ok.textContent = data.message || "出站节点已保存";
                ok.style.display = "block";
                await loadOutboundNodes();
                setTimeout(closeOutboundNodeModal, 450);
            } catch (e) {
                err.textContent = "无法连接后端接口";
                err.style.display = "block";
            } finally {
                btn.disabled = false;
                btn.textContent = "添加节点";
            }
        }

        async function deleteOutboundNode(nodeId) {
            if (!isFeatureEnabled("custom_enabled")) {
                showToast("请先开启自定义节点功能", "warning");
                return;
            }
            if (!confirm("确定删除这个出站节点吗？相关路由规则会同步更新。")) return;
            try {
                const res = await fetch("./api/panel/outbound-nodes/delete", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ id: nodeId })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    alert(data.error || "删除出站节点失败");
                    return;
                }
                await loadOutboundNodes();
                await loadRoutingRules();
            } catch (e) {
                alert("无法连接后端接口");
            }
        }

        async function toggleOutboundNode(nodeId, enabled) {
            if (!isFeatureEnabled("custom_enabled")) {
                showToast("请先开启自定义节点功能", "warning");
                return;
            }
            try {
                const res = await fetch("./api/panel/outbound-nodes/toggle", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ id: nodeId, enabled })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    alert(data.error || "更新出站节点状态失败");
                    return;
                }
                await loadOutboundNodes();
                await loadRoutingRules();
            } catch (e) {
                alert("无法连接后端接口");
            }
        }
        function closeOpenvpnRoutingModal() {
            const modal = $("openvpn-routing-modal");
            if (modal) modal.style.display = "none";
        }

        function updateCountryFilter() {
            const select = $("country_filter");
            if (!select) return;
            const selectedValue = select.value;
            const countries = Array.from(new Set(nodes.map(n => n.country).filter(Boolean))).sort();

            const currentOptions = Array.from(select.options).map(o => o.value).filter(Boolean);
            if (JSON.stringify(countries) === JSON.stringify(currentOptions)) {
                return;
            }

            select.innerHTML = '<option value="">所有国家</option>' +
                countries.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join("");

            if (countries.includes(selectedValue)) {
                select.value = selectedValue;
            } else {
                select.value = "";
            }
        }

        function getFilteredNodes() {
            return nodes.filter(Boolean);
        }

        function stableSortNodes() {
            nodes.sort((a, b) => {
                if (!a || !b) return 0;
                const aScore = a.score || 0;
                const bScore = b.score || 0;
                if (bScore !== aScore) {
                    return bScore - aScore;
                }
                const aId = a.id || "";
                const bId = b.id || "";
                return aId.localeCompare(bId);
            });
        }

        function render() {
            renderFeatureGateSwitches();
            const activeNodeId = state.active_openvpn_node_id;
            const activeNode = nodes.find(n => n && (n.active || n.id === activeNodeId));
            const openvpnEnabled = state.openvpn_enabled === true || state.openvpn_running === true;
            const vpngateFeatureEnabled = isFeatureEnabled("vpngate_enabled");
            const vpngateTableRegion = $("vpngate_table_region");
            const vpngatePaginationRegion = $("vpngate_pagination_region");
            if (vpngateTableRegion) vpngateTableRegion.style.display = vpngateFeatureEnabled ? "block" : "none";
            if (vpngatePaginationRegion) vpngatePaginationRegion.style.display = vpngateFeatureEnabled ? "flex" : "none";

            // Render active node details card
            const activeCardContainer = $("active_node_card");
            if (!vpngateFeatureEnabled) {
                activeCardContainer.innerHTML = featureDisabledHtml("VPNGate 公益节点未启动", "点击右上角启动后会同步公益节点、检测可用出口并允许启动 OpenVPN。", "vpngate_enabled");
            } else if (state.is_connecting && !activeNode) {
                activeCardContainer.innerHTML = `
                    <div class="active-card" style="border-color: var(--yellow); box-shadow: 0 8px 32px rgba(255, 159, 10, 0.12);">
                        <div class="active-card-info">
                            <div style="background: rgba(255, 159, 10, 0.15); border: 1px solid rgba(255, 159, 10, 0.3); width: 44px; height: 44px; border-radius: 10px; display: flex; align-items: center; justify-content: center;">
                                <svg xmlns="http://www.w3.org/2000/svg" style="color: var(--yellow); width: 22px; height: 22px; animation: spin 2s linear infinite;" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 1121.21 8H18" /></svg>
                            </div>
                            <div class="active-card-details">
                                <div class="active-card-title" style="color: var(--yellow);">
                                    <span class="badge warn" style="padding: 2px 8px;"><span class="badge-pulse" style="background: var(--yellow);"></span>正在连接</span>
                                    <strong style="margin-left: 8px;">${esc(state.active_node_latency || '正在建立隧道...')}</strong>
                                </div>
                                <div class="active-card-meta" style="margin-top: 4px;">
                                    ${esc(state.last_check_message || '正在发送握手信息并配置 tun0 虚拟出口网卡，请稍候...')}
                                </div>
                            </div>
                        </div>
                        <div class="active-card-tools">
                            ${proxyStatusPanelHtml(true)}
                            <button class="btn btn-danger active-card-action" onclick="disconnectNode()">停止 OpenVPN</button>
                        </div>
                    </div>
                `;
            } else if (activeNode) {
                const latencyClass = getLatencyClass(activeNode.latency_ms);
                const latencyText = activeNode.latency_ms ? `<span class="latency-val ${latencyClass}">${activeNode.latency_ms} ms</span>` : "-";
                const displayLocation = activeNode.location || translateCountry(activeNode.country) || "-";
                activeCardContainer.innerHTML = `
                    <div class="active-card" style="border-color: var(--green); box-shadow: 0 8px 32px rgba(52, 199, 89, 0.08);">
                        <div class="active-card-info">
                            <div style="background: rgba(52, 199, 89, 0.15); border: 1px solid rgba(52, 199, 89, 0.3); width: 44px; height: 44px; border-radius: 10px; display: flex; align-items: center; justify-content: center;">
                                <svg xmlns="http://www.w3.org/2000/svg" style="color: var(--green); width: 22px; height: 22px;" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
                            </div>
                            <div class="active-card-details">
                                <div class="active-card-title" style="color: var(--green);">
                                    <span class="badge available"><span class="badge-pulse"></span>已连接</span>
                                    <strong style="margin-left: 8px;">${esc(translateCountry(activeNode.country))} 专线节点</strong>
                                </div>
                                <div class="active-card-value mono" style="margin-top: 2px;">
                                    ${esc(activeNode.ip || activeNode.remote_host)}:${activeNode.remote_port || ""}
                                </div>
                                <div class="active-card-meta" style="margin-top: 4px;">
                                    <span>位置: <strong>${esc(displayLocation)}</strong></span>
                                    <span style="margin-left: 12px;">延时: <strong>${latencyText}</strong></span>
                                    <span style="margin-left: 12px;">主体: <strong>${esc(activeNode.owner || activeNode.as_name || "-")}</strong></span>
                                    <span style="margin-left: 12px;">网络: <strong>${esc(translateIpType(activeNode.ip_type))}</strong></span>
                                </div>
                            </div>
                        </div>
                        <div class="active-card-tools">
                            ${proxyStatusPanelHtml(false)}
                            <button class="btn btn-danger active-card-action" onclick="disconnectNode()">停止 OpenVPN</button>
                        </div>
                    </div>
                `;
            } else {
                const idleBadge = openvpnEnabled
                    ? `<span class="badge warn"><span class="badge-pulse" style="background: var(--yellow);"></span>已启用</span>`
                    : `<span class="badge unavailable">未启动</span>`;
                const idleTitle = openvpnEnabled ? "OpenVPN 正在等待可用节点" : "OpenVPN 当前未启动";
                const idleMessage = openvpnEnabled
                    ? (state.last_check_message || "正在同步并筛选 VPNGate 节点，稍候会自动接入可用出口。")
                    : "VPNGate 已开启；OpenVPN 需要在网页手动启动，也可以直接在节点列表选择具体节点。";
                const idleIconColor = openvpnEnabled ? "var(--yellow)" : "var(--red)";
                const idleIconBg = openvpnEnabled ? "rgba(255, 159, 10, 0.12)" : "rgba(255, 69, 58, 0.08)";
                const idleIconBorder = openvpnEnabled ? "rgba(255, 159, 10, 0.24)" : "rgba(255, 69, 58, 0.15)";
                const idleAction = openvpnEnabled
                    ? `<button class="btn btn-danger active-card-action" onclick="disconnectNode()">停止 OpenVPN</button>`
                    : `<button class="btn btn-primary active-card-action" onclick="startOpenvpnService()">启动 OpenVPN</button>`;
                activeCardContainer.innerHTML = `
                    <div class="active-card" style="background: rgba(255, 255, 255, 0.01); border-style: dashed;">
                        <div class="active-card-info">
                            <div style="background: ${idleIconBg}; border: 1px solid ${idleIconBorder}; width: 44px; height: 44px; border-radius: 10px; display: flex; align-items: center; justify-content: center;">
                                <svg xmlns="http://www.w3.org/2000/svg" style="color: ${idleIconColor}; width: 22px; height: 22px;" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
                            </div>
                            <div class="active-card-details">
                                <div class="active-card-title" style="color: var(--muted);">
                                    ${idleBadge}
                                    <strong style="margin-left: 8px;">${idleTitle}</strong>
                                </div>
                                <div class="active-card-meta" style="margin-top: 4px;">
                                    ${esc(idleMessage)}
                                </div>
                            </div>
                        </div>
                        <div class="active-card-tools">
                            ${proxyStatusPanelHtml(!openvpnEnabled)}
                            ${idleAction}
                        </div>
                    </div>
                `;
            }

            const shown = vpngateFeatureEnabled ? getFilteredNodes() : [];

            // Update user tag
            const displayUsername = $("display-username");
            if (displayUsername) {
                displayUsername.innerText = state.username || "admin";
            }

            // Sync the active class of the theme select cards in settings
            const savedThemeName = ['theme-orange', 'theme-red'].includes(localStorage.getItem('theme'))
                ? localStorage.getItem('theme')
                : 'theme-orange';
            document.querySelectorAll('.theme-option').forEach(card => {
                if (card.getAttribute('for') === savedThemeName) {
                    card.classList.add('active');
                } else {
                    card.classList.remove('active');
                }
            });

            // Update proxy test status card based on background checks
            const pBadge = $("proxy_status_badge");
            const pIpVal = $("proxy_ip_val");
            const pLatVal = $("proxy_latency_val");
            const pBtn = $("btn_test_proxy");
            if (pBadge && pIpVal && pLatVal && pBtn) {
                if (!openvpnEnabled && !activeNode) {
                    pBadge.className = "badge unavailable";
                    pBadge.textContent = "未启动";
                    pIpVal.textContent = "-";
                    pLatVal.innerHTML = `<span style="color: var(--muted); font-size: 11.5px;">OpenVPN 未启动</span>`;
                    pBtn.disabled = true;
                } else if (state.is_connecting) {
                    pBadge.className = "badge warn";
                    pBadge.innerHTML = `<span class="badge-pulse"></span>连接中`;
                    pIpVal.textContent = state.active_node_latency || "正在连接...";
                    pLatVal.innerHTML = `<span style="color: var(--muted); font-size: 11.5px;">${esc(state.last_check_message || "建立隧道中...")}</span>`;
                    pBtn.disabled = true;
                } else {
                    pBtn.disabled = false;
                    if (state.proxy_ok !== undefined) {
                        if (state.proxy_ok) {
                            pBadge.className = "badge available";
                            pBadge.textContent = "可用";
                            pIpVal.textContent = state.proxy_ip || "-";
                            const latencyClass = getLatencyClass(state.proxy_latency_ms);
                            pLatVal.innerHTML = `<span class="latency-val ${latencyClass}">${state.proxy_latency_ms} ms</span>`;
                        } else {
                            pBadge.className = "badge unavailable";
                            pBadge.textContent = "不可用";
                            pIpVal.textContent = "-";
                            pLatVal.innerHTML = `<span class="latency-val latency-poor" style="font-size:11px;" title="${esc(state.proxy_error)}">连通失败</span>`;
                        }
                    } else {
                        pBadge.className = "badge not_checked";
                        pBadge.textContent = "未检测";
                        pIpVal.textContent = "-";
                        pLatVal.innerHTML = state.last_check_message ? `<span style="color: var(--muted); font-size: 11.5px;">${esc(state.last_check_message)}</span>` : "";
                    }
                }
            }

            // Pagination calculation
            const totalPages = Math.ceil(shown.length / pageSize) || 1;
            if (currentPage > totalPages) currentPage = totalPages;
            if (currentPage < 1) currentPage = 1;

            const startIndex = (currentPage - 1) * pageSize;
            const endIndex = Math.min(startIndex + pageSize, shown.length);
            currentPageNodes = shown.slice(startIndex, endIndex);

            // Render table rows
            if (!vpngateFeatureEnabled) {
                return;
            }
            if (currentPageNodes.length === 0) {
                $("rows").innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--muted); padding: 40px 0;">${vpngateFeatureEnabled ? "暂无可用的 VPNGate 备选节点。" : "VPNGate 公益节点未开启，暂不加载节点资源。"}</td></tr>`;
            } else {
                $("rows").innerHTML = currentPageNodes.map(n => {
                    if (!n) return '';
                    const isCurrentlyActive = activeNode && n.id === activeNode.id;
                    const rowClass = isCurrentlyActive ? 'class="bg-[color-mix(in_srgb,var(--second)_6%,transparent)] outline outline-2 outline-second outline-offset-[-2px] relative"' : '';

                    const badgeClass = isCurrentlyActive ? 'available' : (n.probe_status || 'not_checked');
                    const badgeText = isCurrentlyActive ? '<span class="badge-pulse"></span>已连接' : translateStatus(n.probe_status);
                    const displayLocation = n.location || translateCountry(n.country) || "-";
                    const ipText = `${n.ip || n.remote_host || "-"}${n.remote_port ? ":" + n.remote_port : ""}`;
                    const ispText = n.owner || n.as_name || "-";
                    const asnText = n.asn ? `AS${String(n.asn).replace(/^AS/i, "")}` : "-";

                    const isUnavailable = n.probe_status === "unavailable";
                    const connectLabel = openvpnEnabled ? "切换" : "启动";
                    const connectBtn = isCurrentlyActive
                        ? `<button class="btn btn-primary btn-sm" disabled>已连接</button>`
                        : `<button class="btn btn-secondary btn-sm" ${(isUnavailable || state.is_connecting) ? 'disabled' : ''} onclick="${esc(`connectNode(${jsArg(n.id)})`)}">${connectLabel}</button>`;

                    return `<tr ${rowClass}>
                        <td>
                            <strong>${esc(displayLocation)}</strong>
                            <div style="font-size:12px; color:var(--muted); margin-top:3px;">${esc(n.country_short || n.country || "-")}</div>
                        </td>
                        <td class="mono">${esc(ipText)}</td>
                        <td>
                            <div class="mono" style="font-size:12px; color:var(--muted);">${esc(asnText)}</div>
                            <strong>${esc(ispText)}</strong>
                        </td>
                        <td>
                            <div style="display:flex; justify-content:flex-end; align-items:center; gap:8px; flex-wrap:wrap;">
                                <span class="badge ${badgeClass}">${badgeText}</span>
                                ${connectBtn}
                            </div>
                        </td>
                    </tr>`;
                }).join("");
            }

            // Render pagination controls
            $("page_start").textContent = shown.length > 0 ? startIndex + 1 : 0;
            $("page_end").textContent = endIndex;
            $("filtered_count").textContent = shown.length;
            $("current_page_val").textContent = currentPage;
            $("total_pages_val").textContent = totalPages;

            $("btn_first_page").disabled = currentPage === 1;
            $("btn_prev_page").disabled = currentPage === 1;
            $("btn_next_page").disabled = currentPage === totalPages;
            $("btn_last_page").disabled = currentPage === totalPages;
        }

        $("btn_first_page").onclick = () => { currentPage = 1; render(); };
        $("btn_prev_page").onclick = () => { if (currentPage > 1) { currentPage--; render(); } };
        $("btn_next_page").onclick = () => {
            const shown = getFilteredNodes();
            const totalPages = Math.ceil(shown.length / pageSize) || 1;
            if (currentPage < totalPages) { currentPage++; render(); }
        };
        $("btn_last_page").onclick = () => {
            const shown = getFilteredNodes();
            const totalPages = Math.ceil(shown.length / pageSize) || 1;
            currentPage = totalPages;
            render();
        };

        async function connectNode(id) {
            if (!isFeatureEnabled("vpngate_enabled")) {
                showToast("请先开启 VPNGate 公益节点功能", "warning");
                return;
            }
            state.is_connecting = true;
            state.openvpn_enabled = true;
            state.active_openvpn_node_id = id;
            state.active_node_latency = "正在连接";
            state.last_check_message = "正在发送连接请求...";
            render();

            startConnectionPolling();

            try {
                const r = await fetch("./api/connect", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ id })
                });
                const result = await r.json();
                if (!result.ok) {
                    alert("连接失败: " + (result.error || "未知错误"));
                    if (pollInterval) {
                        clearInterval(pollInterval);
                        pollInterval = null;
                    }
                    state.is_connecting = false;
                    render();
                    return;
                }
            } catch (e) {
                alert("连接请求错误");
                if (pollInterval) {
                    clearInterval(pollInterval);
                    pollInterval = null;
                }
                state.is_connecting = false;
                render();
            }
        }

        async function syncVpngateNodes() {
            if (!isFeatureEnabled("vpngate_enabled")) {
                showToast("请先开启 VPNGate 公益节点功能", "warning");
                return;
            }
            const btn = $("vpngate_sync_btn") || $("refresh");
            btn.disabled = true;
            btn.textContent = "同步中...";
            try {
                await fetch("./api/refresh_nodes", { method: "POST" });
                await load();
            } catch (e) {
                showToast("同步节点失败，请检查后端服务", "error");
            } finally {
                btn.disabled = false;
                btn.textContent = "同步节点";
            }
        }

        const searchInput = $("search");
        if (searchInput) searchInput.oninput = () => { currentPage = 1; render(); };
        const countryFilter = $("country_filter");
        if (countryFilter) countryFilter.onchange = () => { currentPage = 1; render(); };

        if ($("refresh")) $("refresh").onclick = syncVpngateNodes;
        window.syncVpngateNodes = syncVpngateNodes;


        async function saveNetwork(e) {
            e.preventDefault();
            const errorDivEl = $("network_error");
            const successDiv = $("network_success");
            const submitBtn = $("network_submit_btn");

            errorDivEl.style.display = "none";
            successDiv.style.display = "none";

            const proxyPort = parseInt($("net_proxy_port").value);
            const routingModeRadio = document.querySelector('input[name="net_routing_mode"]:checked');
            const routingMode = routingModeRadio ? routingModeRadio.value : "auto";
            const forceCountry = $("net_force_country").value;

            if (isNaN(proxyPort) || proxyPort < 1024 || proxyPort > 65535) {
                errorDivEl.textContent = "代理出站端口范围必须在 1024 至 65535 之间";
                errorDivEl.style.display = "block";
                return;
            }

            if (routingMode === "fixed_region" && !forceCountry) {
                errorDivEl.textContent = "请选择一个要锁定的目标国家";
                errorDivEl.style.display = "block";
                return;
            }

            submitBtn.disabled = true;
            submitBtn.textContent = "正在保存...";

            try {
                const res = await fetch("./api/update_settings", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        proxy_port: proxyPort,
                        routing_mode: routingMode,
                        force_country: forceCountry
                    })
                });

                const data = await res.json();
                if (res.ok && data.ok) {
                    successDiv.textContent = "配置保存成功，已即时生效！";
                    successDiv.style.display = "block";
                    setTimeout(() => {
                        successDiv.style.display = "none";
                        submitBtn.disabled = false;
                        submitBtn.textContent = "保存设置";
                        closeOpenvpnRoutingModal();
                        load();
                    }, 1500);
                } else {
                    errorDivEl.textContent = data.error || "保存失败，请检查输入";
                    errorDivEl.style.display = "block";
                    submitBtn.disabled = false;
                    submitBtn.textContent = "保存设置";
                }
            } catch (err) {
                errorDivEl.textContent = "连接服务器失败，请稍后重试";
                errorDivEl.style.display = "block";
                submitBtn.disabled = false;
                submitBtn.textContent = "保存设置";
            }
        }
