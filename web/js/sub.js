
        function generateRandomUUID() {
            if (window.crypto && window.crypto.randomUUID) {
                return window.crypto.randomUUID();
            }
            // RFC4122 v4 compliant fallback for non-secure HTTP contexts
            return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
                const r = Math.random() * 16 | 0;
                const v = c === 'x' ? r : (r & 0x3 | 0x8);
                return v.toString(16);
            });
        }

        function encodeBase64(str) {
            const utf8Bytes = new TextEncoder().encode(str);
            const binaryStr = Array.from(utf8Bytes).map(b => String.fromCharCode(b)).join("");
            return btoa(binaryStr);
        }

        function openShareModal(ibIdx, clientIdx) {
            if (!xrayConfig) return;
            const inbound = xrayConfig.inbounds[ibIdx];
            if (!inbound) return;
            const client = inbound.clients[clientIdx];
            if (!client) return;

            const protocol = inbound.protocol.toLowerCase();
            const port = inbound.port;
            const network = inbound.network.toLowerCase();
            const ws_path = inbound.ws_path || "/";
            const name = client.name || "client";

            const clientSecret = protocol === "vless" || protocol === "vmess" ? (client.uuid || "") : (client.password || "");
            const host = location.hostname;
            const remark = `${name}@${protocol.toUpperCase()}_${port}`;

            let nodeUrl = "";
            if (protocol === "vless") {
                let rawUrl = `vless://${clientSecret}@${host}:${port}?type=${network}&security=none`;
                if (network === "ws") rawUrl += `&path=${encodeURIComponent(ws_path)}`;
                rawUrl += `#${encodeURIComponent(remark)}`;
                nodeUrl = encodeBase64(rawUrl);
            } else if (protocol === "vmess") {
                const vmessJson = {
                    v: "2",
                    ps: remark,
                    add: host,
                    port: String(port),
                    id: clientSecret,
                    aid: "0",
                    scy: "auto",
                    net: network,
                    type: "none",
                    host: "",
                    path: network === "ws" ? ws_path : "",
                    tls: "none"
                };
                const utf8Bytes = new TextEncoder().encode(JSON.stringify(vmessJson));
                const binaryStr = Array.from(utf8Bytes).map(b => String.fromCharCode(b)).join("");
                nodeUrl = "vmess://" + btoa(binaryStr);
            } else if (protocol === "trojan") {
                let rawUrl = `trojan://${clientSecret}@${host}:${port}?type=${network}&security=none`;
                if (network === "ws") rawUrl += `&path=${encodeURIComponent(ws_path)}`;
                rawUrl += `#${encodeURIComponent(remark)}`;
                nodeUrl = encodeBase64(rawUrl);
            } else if (protocol === "shadowsocks") {
                const cipher = inbound.encryption || "aes-256-gcm";
                const credentials = `${cipher}:${clientSecret}`;
                const utf8Bytes = new TextEncoder().encode(credentials);
                const binaryStr = Array.from(utf8Bytes).map(b => String.fromCharCode(b)).join("");
                const b64Cred = btoa(binaryStr).replace(/=/g, "");
                const rawUrl = `ss://${b64Cred}@${host}:${port}#${encodeURIComponent(remark)}`;
                nodeUrl = encodeBase64(rawUrl);
            }

            const subUrl = `${location.origin}/api/xray/subscribe?token=${clientSecret}`;

            document.getElementById("share-node-url").value = nodeUrl;
            document.getElementById("share-sub-url").value = subUrl;

            const statusEl = document.getElementById("share-client-status");
            const expiryEl = document.getElementById("share-client-expiry");
            const isExpired = client.expiry_time > 0 && ((Date.now() / 1000) > client.expiry_time);

            if (client.status === "disabled") {
                statusEl.innerText = "已禁用";
                statusEl.style.color = "var(--red)";
            } else if (isExpired) {
                statusEl.innerText = "已过期";
                statusEl.style.color = "var(--red)";
            } else {
                statusEl.innerText = "活跃";
                statusEl.style.color = "var(--green)";
            }

            expiryEl.innerText = client.expiry_time > 0 ? new Date(client.expiry_time * 1000).toLocaleDateString() : "永不过期";
            if (isExpired) expiryEl.style.color = "var(--red)";
            else expiryEl.style.color = "";

            const qrContainer = document.getElementById("share-qrcode-container");
            qrContainer.innerHTML = `<img src="https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(nodeUrl)}" style="width:200px; height:200px; display:block; border-radius:4px;" alt="QR Code">`;

            const modal = document.getElementById("share-modal");
            if (modal) modal.style.display = "flex";
        }

        function closeShareModal() {
            const modal = document.getElementById("share-modal");
            if (modal) modal.style.display = "none";
        }

        function showQRCodeViewModal(title, url) {
            const modal = $("qrcode-view-modal");
            const titleEl = $("qrcode-view-title");
            const imgEl = $("qrcode-view-img");
            const urlEl = $("qrcode-view-url");
            if (!modal || !titleEl || !imgEl || !urlEl) return;

            titleEl.textContent = title;
            urlEl.value = url;
            imgEl.src = `https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=${encodeURIComponent(url)}`;
            modal.style.display = "flex";
        }

        function closeQRCodeViewModal() {
            const modal = $("qrcode-view-modal");
            if (modal) modal.style.display = "none";
        }

        async function copyQRCodeViewUrl() {
            const urlEl = $("qrcode-view-url");
            if (!urlEl) return;
            try {
                await copyToClipboard(urlEl.value);
                showToast("链接已成功复制到剪贴板！", "success");
            } catch (e) {
                showToast("复制失败，请重试", "error");
            }
        }

        function showSubscriptionLinkQRCode(linkId) {
            const link = subscriptionLinks.find(item => item.id === linkId);
            if (!link) return;
            const url = subscriptionUrl(link);
            showQRCodeViewModal(`“${link.name || '订阅'}” 二维码`, url);
        }

        async function showSubscriptionNodeQRCode(nodeId) {
            try {
                const res = await fetch("./api/panel/subscription-nodes/share-link", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ id: nodeId })
                });
                const data = await res.json();
                if (!res.ok || !data.ok || !data.node || !data.node.link) {
                    showToast(data.error || "获取节点分享链接失败", "error");
                    return;
                }
                showQRCodeViewModal(`“${data.node.name || '节点'}” 二维码`, data.node.link);
            } catch (e) {
                showToast("无法连接后端接口", "error");
            }
        }

        let subscriptionLinks = [];
        let subscriptionNodes = [];
        let selectedSubscriptionLinkId = "";
        let createLinkPreferredSubscriptionId = "";
        const expandedSubscriptionLinks = new Set();
        const subscriptionProtocolNames = {
            "vless-reality": "VLESS-Reality",
            "vmess-ws-tls": "VMess + WS + TLS",
            "socks5": "SOCKS5"
        };
        const camouflageHosts = [
            "www.microsoft.com",
            "www.apple.com",
            "www.cloudflare.com",
            "www.bing.com",
            "www.mozilla.org",
            "www.intel.com",
            "www.nvidia.com",
            "www.amd.com",
            "www.cisco.com",
            "www.samsung.com",
            "www.hp.com",
            "www.tesla.com",
            "www.paypal.com",
            "www.speedtest.net",
            "www.python.org",
            "www.amazon.com",
            "www.oracle.com",
            "www.adobe.com",
            "www.zoom.us",
            "www.ebay.com",
            "www.ibm.com",
            "www.visa.com",
            "www.mastercard.com"
        ];

        function selectedSubscriptionLink() {
            return subscriptionLinks.find(item => item.id === selectedSubscriptionLinkId) || null;
        }

        function subscriptionNodesForLink(linkId) {
            return subscriptionNodes.filter(node => String(node.subscription_id || "") === String(linkId || ""));
        }

        function independentSubscriptionNodes() {
            return subscriptionNodes.filter(node => !node.subscription_id);
        }

        function subscriptionLinkNameById(id) {
            const link = subscriptionLinks.find(item => item.id === id);
            return link ? link.name : (id || "-");
        }

        function subscriptionUrl(link) {
            if (!link || !link.token) return "";
            return `${window.location.origin}/api/xray/subscribe?token=${encodeURIComponent(link.token)}`;
        }

        function openCreateLinkModal(preferredSubscriptionId = "") {
            createLinkPreferredSubscriptionId = preferredSubscriptionId || "";
            if (createLinkPreferredSubscriptionId) {
                selectedSubscriptionLinkId = createLinkPreferredSubscriptionId;
                renderSubscriptionItems();
            }
            const modal = $("create-link-modal");
            if (modal) modal.style.display = "flex";
        }

        function closeCreateLinkModal() {
            const modal = $("create-link-modal");
            if (modal) modal.style.display = "none";
        }

        function createNodeFromChooser(protocol) {
            const linkId = createLinkPreferredSubscriptionId;
            closeCreateLinkModal();
            openSubscriptionNodeModal("", linkId, protocol);
        }

        async function loadSubscriptionLinks() {
            const container = $("subscription_items_container");
            if (container) container.innerHTML = `<div class="text-center py-8 text-muted">正在加载入站列表...</div>`;
            try {
                const res = await fetch("./api/panel/subscription-links");
                const data = await res.json();
                subscriptionLinks = Array.isArray(data.subscriptions) ? data.subscriptions : [];
                if (subscriptionLinks.length && !subscriptionLinks.some(item => item.id === selectedSubscriptionLinkId)) {
                    selectedSubscriptionLinkId = subscriptionLinks[0].id;
                }
                if (!subscriptionLinks.length) selectedSubscriptionLinkId = "";
                renderSubscriptionItems();
            } catch (e) {
                if (container) container.innerHTML = `<div class="text-center py-8 text-danger">入站列表加载失败</div>`;
            }
        }

        async function loadSubscriptionNodes() {
            try {
                const res = await fetch("./api/panel/subscription-nodes");
                const data = await res.json();
                subscriptionNodes = Array.isArray(data.nodes) ? data.nodes : [];
                renderSubscriptionItems();
            } catch (e) {
                const container = $("subscription_items_container");
                if (container) container.innerHTML = `<div class="text-center py-8 text-danger">入站列表加载失败</div>`;
            }
        }

        async function loadSubscriptionWorkspace() {
            const container = $("subscription_items_container");
            if (container) container.innerHTML = `<div class="text-center py-8 text-muted">正在加载入站列表...</div>`;
            try {
                const [linksRes, nodesRes, rulesRes, outNodesRes] = await Promise.all([
                    fetch("./api/panel/subscription-links"),
                    fetch("./api/panel/subscription-nodes"),
                    fetch("./api/panel/routing-rules"),
                    fetch("./api/panel/outbound-nodes")
                ]);
                const linksData = await linksRes.json();
                const nodesData = await nodesRes.json();
                const rulesData = await rulesRes.json();
                const outData = await outNodesRes.json();
                subscriptionLinks = Array.isArray(linksData.subscriptions) ? linksData.subscriptions : [];
                subscriptionNodes = Array.isArray(nodesData.nodes) ? nodesData.nodes : [];
                if (typeof routingRules !== "undefined") routingRules = Array.isArray(rulesData.rules) ? rulesData.rules : [];
                if (typeof outboundNodes !== "undefined") outboundNodes = Array.isArray(outData.nodes) ? outData.nodes : [];
                if (subscriptionLinks.length && !subscriptionLinks.some(item => item.id === selectedSubscriptionLinkId)) {
                    selectedSubscriptionLinkId = subscriptionLinks[0].id;
                }
                if (!subscriptionLinks.length) selectedSubscriptionLinkId = "";
                renderSubscriptionItems();
            } catch (e) {
                if (container) container.innerHTML = `<div class="text-center py-8 text-danger">入站列表加载失败</div>`;
            }
        }

        function outboundLabelById(id) {
            if (!id) return "";
            const virtualMap = {
                "vpn-out": "系统默认出口",
                "vpngate-openvpn-active": "VPNGate 当前 OpenVPN",
                "warp": "Cloudflare WARP"
            };
            if (virtualMap[id]) return virtualMap[id];
            if (typeof outboundNodes !== "undefined" && Array.isArray(outboundNodes)) {
                const node = outboundNodes.find(item => item.id === id || item.tag === id);
                if (node) return node.name || node.tag || node.id;
            }
            return id;
        }

        function routedOutboundsForSubscriptionNode(nodeId) {
            if (typeof routingRules === "undefined" || !Array.isArray(routingRules)) return [];
            const node = subscriptionNodes.find(item => String(item.id) === String(nodeId));
            const parentId = node ? String(node.subscription_id || "") : "";
            const matched = [];
            routingRules.forEach(rule => {
                if (rule.enabled === false) return;
                const inboundIds = asArray(rule.inbound_node_ids || rule.inbound_node_id).map(String);
                if (!inboundIds.includes(String(nodeId)) && (!parentId || !inboundIds.includes(parentId))) return;
                const outboundIds = asArray(rule.outbound_node_ids || rule.outbound_node_id).map(String);
                outboundIds.forEach(id => {
                    if (id && !matched.includes(id)) matched.push(id);
                });
            });
            return matched;
        }

        function renderSubscriptionItems() {
            const container = $("subscription_items_container");
            const hint = $("subscription_items_hint");
            if (!container) return;
            const subscribedCount = subscriptionNodes.filter(node => node.subscription_id).length;
            const independentCount = subscriptionNodes.length - subscribedCount;
            if (hint) {
                hint.textContent = `${subscriptionLinks.length} 个订阅链接，${subscriptionNodes.length} 个节点链接，${independentCount} 个独立节点`;
            }
            
            const rowIcon = (name) => {
                const icons = {
                    add: `<path d="M12 5v14M5 12h14"></path>`,
                    star: `<path d="m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.8 1-6.1-4.4-4.3 6.1-.9Z"></path>`,
                    copy: `<rect x="9" y="9" width="11" height="11" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>`,
                    qr: `<rect x="3" y="3" width="6" height="6" rx="1"></rect><rect x="15" y="3" width="6" height="6" rx="1"></rect><rect x="3" y="15" width="6" height="6" rx="1"></rect><path d="M15 15h2v2h-2z"></path><path d="M19 15h2"></path><path d="M15 19h6"></path><path d="M11 3h1"></path><path d="M11 7h1"></path><path d="M3 11h1"></path><path d="M7 11h1"></path>`,
                    power: `<path d="M12 2v10M18.4 6.6a9 9 0 1 1-12.8 0"></path>`,
                    edit: `<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"></path>`,
                    trash: `<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v5M14 11v5"></path>`
                };
                return `<svg class="row-action__icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">${icons[name] || icons.edit}</svg>`;
            };
            
            const actionButton = (label, icon, onclick, danger = false, showText = false, extraClass = '') => `
                <button type="button" class="row-action-btn ${extraClass}${danger ? " is-danger" : ""}${showText ? " row-action-btn--text" : ""}" onclick="${esc(onclick)}" title="${esc(label)}" aria-label="${esc(label)}">
                    ${rowIcon(icon)}${showText ? `<span class="row-action__label">${esc(label)}</span>` : ""}
                </button>
            `;

            const renderNodeCard = (node, nested = false) => {
                const enabled = node.enabled === true;
                const statusText = enabled ? "已启动" : "已停止";
                const protocolName = subscriptionProtocolNames[node.protocol] || node.protocol || "-";
                const linkName = node.subscription_id ? subscriptionLinkNameById(node.subscription_id) : "独立节点";
                const routedOutboundIds = routedOutboundsForSubscriptionNode(node.id);
                const outboundText = routedOutboundIds.length
                    ? routedOutboundIds.map(outboundLabelById).join(",")
                    : (node.outbound_node_id ? outboundLabelById(node.outbound_node_id) : "未绑定");

                const actionsHtml = [
                    actionButton("复制链接", "copy", `copySubscriptionNodeUrl(${jsArg(node.id)})`),
                    actionButton("二维码", "qr", `showSubscriptionNodeQRCode(${jsArg(node.id)})`),
                    actionButton("编辑", "edit", `editSubscriptionNode(${jsArg(node.id)})`),
                    actionButton(enabled ? "停用" : "启用", "power", `toggleSubscriptionNode(${jsArg(node.id)}, ${enabled ? "false" : "true"})`, false, false, enabled ? 'text-success' : 'text-muted'),
                    actionButton("删除", "trash", `deleteSubscriptionNode(${jsArg(node.id)})`, true)
                ].join("");

                return `
                    <div class="node-card bg-[rgba(255,255,255,0.015)] border border-[color-mix(in_srgb,var(--border)_20%,transparent)] rounded-lg py-1.5 px-3 flex items-center justify-between gap-3 hover:bg-[rgba(255,255,255,0.04)] transition-all duration-200 w-full">
                        <div class="flex items-center gap-2.5 min-w-0">
                            <!-- Status dot -->
                            <span class="w-2 h-2 rounded-full ${enabled ? 'bg-[var(--success)] shadow-[0_0_6px_var(--success)]' : 'bg-[var(--muted)]'} flex-none" title="${statusText}"></span>
                            
                            <!-- Horizontal info list -->
                            <div class="flex items-center gap-3 text-[12.5px] flex-wrap text-text min-w-0">
                                <strong class="text-[13px] font-semibold text-text truncate max-w-[150px]" title="${esc(node.name || '-')}">${esc(node.name || "-")}</strong>
                                <span class="px-1.5 py-0.5 rounded bg-[rgba(255,255,255,0.06)] border border-[color-mix(in_srgb,var(--border)_20%,transparent)] text-muted text-[11px] font-mono leading-none">${esc(protocolName)}</span>
                                <span class="text-muted text-[12px] font-mono">端口: ${esc(node.port || "-")}</span>
                                <span class="text-muted text-[12px]">(出站: <span class="text-text font-medium">${esc(outboundText)}</span>)</span>
                                ${!nested ? `<span class="text-muted text-[12px]">归属: <span class="text-text">${esc(linkName)}</span></span>` : ''}
                            </div>
                        </div>
                        
                        <!-- Actions -->
                        <div class="flex items-center gap-1 flex-none" onclick="event.stopPropagation()">
                            ${actionsHtml}
                        </div>
                    </div>
                `;
            };

            const renderSubscriptionCard = (link, idx) => {
                const enabled = link.enabled !== false;
                const statusText = enabled ? "已启动" : "已停止";
                const childNodes = subscriptionNodesForLink(link.id);
                const expanded = expandedSubscriptionLinks.has(link.id);
                
                const actionsHtml = [
                    actionButton("添加节点", "add", `openSubscriptionNodeModal('', '${esc(link.id)}', '${esc(link.protocol)}')`),
                    actionButton("复制链接", "copy", `copySubscriptionUrl('${esc(link.id)}')`),
                    actionButton("二维码", "qr", `showSubscriptionLinkQRCode('${esc(link.id)}')`),
                    actionButton("编辑", "edit", `openSubscriptionLinkModal('${esc(link.id)}')`),
                    actionButton(enabled ? "停用" : "启用", "power", `toggleSubscriptionLink('${esc(link.id)}', ${enabled ? "false" : "true"})`, false, false, enabled ? 'text-success' : 'text-muted'),
                    actionButton("删除", "trash", `deleteSubscriptionLink('${esc(link.id)}')`, true)
                ].join("");

                const expandIcon = expanded 
                    ? `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.3" viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"></path></svg>`
                    : `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.3" viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"></path></svg>`;

                const nodesListHtml = childNodes.length 
                    ? childNodes.map(node => renderNodeCard(node, true)).join("")
                    : `<div class="text-center py-3 text-muted text-[12.5px] bg-[rgba(0,0,0,0.01)] rounded-lg border border-dashed border-border">这个订阅链接下还没有节点链接</div>`;

                return `
                    <div class="sub-link-card bg-glass border border-border rounded-2xl p-4 shadow-soft-shadow hover:shadow-shadow transition-all duration-300 flex flex-col gap-3">
                        <!-- Card Header -->
                        <div class="flex items-center justify-between gap-3 w-full">
                            <div class="flex items-center gap-3 min-w-0">
                                <!-- Serial Number -->
                                <span class="text-[13.5px] font-mono text-muted font-bold flex-none">${idx}</span>
                                
                                <!-- Expand Button -->
                                <button type="button" class="w-6 h-6 flex items-center justify-center rounded-md bg-glass border border-border text-muted transition-all duration-200 hover:text-primary hover:border-primary flex-none" onclick="${esc(`toggleSubscriptionExpand(${jsArg(link.id)}, event)`)}" aria-label="${expanded ? '折叠' : '展开'}">
                                    ${expandIcon}
                                </button>
                                
                                <!-- Name -->
                                <span class="font-bold text-[14.5px] text-text truncate max-w-[200px]" title="${esc(link.name || '-')}">${esc(link.name || "-")}</span>
                                
                                <!-- Status Text -->
                                <span class="text-[12px] font-semibold ${enabled ? 'text-success' : 'text-muted'} flex-none">${statusText}</span>
                            </div>
                            
                            <!-- Actions -->
                            <div class="flex items-center gap-1 flex-none" onclick="event.stopPropagation()">
                                ${actionsHtml}
                            </div>
                        </div>
                        
                        <!-- Collapsible Body -->
                        ${expanded ? `
                        <div class="sub-card-nodes-list border-t border-[color-mix(in_srgb,var(--surface-line)_50%,transparent)] pt-3 flex flex-col gap-2.5 animate-[fadeIn_200ms_ease]">
                            ${nodesListHtml}
                        </div>
                        ` : ''}
                    </div>
                `;
            };

            if (!subscriptionLinks.length && !subscriptionNodes.length) {
                container.innerHTML = `<div class="text-center py-10 text-muted bg-glass border border-dashed border-border rounded-[22px]">暂无入站；点击右上角添加入站</div>`;
                return;
            }

            const cards = [];
            
            // 1. Render Subscriptions Cards
            subscriptionLinks.forEach((link, idx) => {
                cards.push(renderSubscriptionCard(link, idx + 1));
            });
            
            // 2. Render Independent Nodes Group
            const independentNodes = independentSubscriptionNodes();
            if (independentNodes.length) {
                cards.push(`
                    <div class="mt-6">
                        <div class="flex items-center gap-2 mb-3 px-1">
                            <h4 class="text-[14px] font-bold text-text">独立节点</h4>
                            <span class="text-[11px] bg-glass border border-border text-muted px-2 py-0.5 rounded-md">${independentNodes.length}</span>
                        </div>
                        <div class="flex flex-col gap-3">
                            ${independentNodes.map(node => renderNodeCard(node, false)).join("")}
                        </div>
                    </div>
                `);
            }
            
            container.innerHTML = cards.join("");
        }

        function renderSubscriptionLinks() {
            renderSubscriptionItems();
        }

        function renderSubscriptionNodes() {
            renderSubscriptionItems();
        }

        function selectSubscriptionLink(linkId) {
            selectedSubscriptionLinkId = linkId || "";
            renderSubscriptionItems();
        }

        function toggleSubscriptionExpand(linkId, event) {
            if (event) event.stopPropagation();
            if (expandedSubscriptionLinks.has(linkId)) {
                expandedSubscriptionLinks.delete(linkId);
            } else {
                expandedSubscriptionLinks.add(linkId);
            }
            renderSubscriptionItems();
        }

        function openSubscriptionLinkModal(linkId = "") {
            const link = subscriptionLinks.find(item => item.id === linkId) || null;
            $("subscription_link_id").value = link ? link.id : "";
            $("subscription_link_name").value = link ? (link.name || "") : "";
            $("subscription_link_token").value = link ? (link.token || "") : "";
            $("subscription_link_remark").value = link ? (link.remark || "") : "";
            $("subscription_link_port").value = link ? (link.port || "") : "";
            $("subscription_link_protocol").value = link ? (link.protocol || "vless-reality") : "vless-reality";
            $("subscription_link_camouflage").value = link ? (link.camouflage_host || "") : "";
            $("subscription_link_ws_path").value = link ? (link.ws_path || "/") : "/";
            
            $("subscription_link_error").style.display = "none";
            $("subscription_link_success").style.display = "none";
            
            const titleEl = $("subscription_link_modal_title");
            if (titleEl) titleEl.textContent = link ? "编辑入站订阅" : "配置入站订阅";
            
            const btn = $("subscription_link_submit");
            if (btn) btn.textContent = link ? "保存配置" : "创建配置";
            
            if (!link) {
                generateSubscriptionToken();
                generateSubscriptionLinkPort();
                generateSubscriptionLinkCamouflage();
            }
            
            handleSubscriptionLinkProtocolChange(link ? (link.protocol || "vless-reality") : "vless-reality");
            
            const modal = $("subscription-link-modal");
            if (modal) modal.style.display = "flex";
        }

        function closeSubscriptionLinkModal() {
            const modal = $("subscription-link-modal");
            if (modal) modal.style.display = "none";
        }

        function generateSubscriptionToken() {
            const input = $("subscription_link_token");
            if (!input) return;
            const randomPart = generateRandomUUID().replaceAll("-", "");
            input.value = `sub_${randomPart.slice(0, 28)}`;
        }

        function generateSubscriptionLinkPort() {
            const input = $("subscription_link_port");
            if (!input) return;
            let port;
            const usedPorts = new Set(subscriptionLinks.map(l => parseInt(l.port)).filter(Boolean));
            do {
                port = Math.floor(Math.random() * (60000 - 10000 + 1)) + 10000;
            } while (usedPorts.has(port));
            input.value = port;
        }

        function generateSubscriptionLinkCamouflage() {
            const input = $("subscription_link_camouflage");
            if (!input) return;
            const idx = Math.floor(Math.random() * camouflageHosts.length);
            input.value = camouflageHosts[idx];
        }

        function handleSubscriptionLinkProtocolChange(protocol) {
            const camoGroup = $("subscription_link_camouflage_group");
            const wsGroup = $("subscription_link_ws_path_group");
            if (protocol === "vless-reality") {
                if (camoGroup) camoGroup.style.display = "block";
                if (wsGroup) wsGroup.style.display = "none";
            } else if (protocol === "vmess-ws-tls") {
                if (camoGroup) camoGroup.style.display = "block";
                if (wsGroup) wsGroup.style.display = "block";
            } else if (protocol === "socks5") {
                if (camoGroup) camoGroup.style.display = "none";
                if (wsGroup) wsGroup.style.display = "none";
            }
        }

        async function saveSubscriptionLink(event) {
            event.preventDefault();
            const err = $("subscription_link_error");
            const ok = $("subscription_link_success");
            const btn = $("subscription_link_submit");
            err.style.display = "none";
            ok.style.display = "none";
            const existing = subscriptionLinks.find(item => item.id === $("subscription_link_id").value);
            const protocol = $("subscription_link_protocol").value;
            const payload = {
                id: $("subscription_link_id").value,
                name: $("subscription_link_name").value.trim(),
                token: $("subscription_link_token").value.trim(),
                remark: $("subscription_link_remark").value.trim(),
                port: parseInt($("subscription_link_port").value),
                protocol: protocol,
                camouflage_host: protocol === "socks5" ? "" : $("subscription_link_camouflage").value.trim(),
                ws_path: protocol === "vmess-ws-tls" ? $("subscription_link_ws_path").value.trim() : "/",
                enabled: existing ? existing.enabled !== false : true,
                created_at: existing ? existing.created_at : ""
            };
            btn.disabled = true;
            btn.textContent = "保存中...";
            try {
                const res = await fetch("./api/panel/subscription-links", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    err.textContent = data.error || "保存订阅链接失败";
                    err.style.display = "block";
                    return;
                }
                selectedSubscriptionLinkId = data.subscription && data.subscription.id ? data.subscription.id : selectedSubscriptionLinkId;
                ok.textContent = data.message || "订阅链接已保存";
                ok.style.display = "block";
                await loadSubscriptionWorkspace();
                setTimeout(closeSubscriptionLinkModal, 450);
            } catch (e) {
                err.textContent = "无法连接后端接口";
                err.style.display = "block";
            } finally {
                btn.disabled = false;
                btn.textContent = $("subscription_link_id").value ? "保存配置" : "创建配置";
            }
        }

        async function deleteSubscriptionLink(linkId) {
            const link = subscriptionLinks.find(item => item.id === linkId);
            const nodeCount = subscriptionNodes.filter(node => node.subscription_id === linkId).length;
            if (!confirm(`确定删除订阅链接“${link ? link.name : linkId}”吗？其中 ${nodeCount} 个节点链接也会一起删除。`)) return;
            try {
                const res = await fetch("./api/panel/subscription-links/delete", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ id: linkId })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    showToast(data.error || "删除订阅链接失败", "error");
                    return;
                }
                if (selectedSubscriptionLinkId === linkId) selectedSubscriptionLinkId = "";
                await loadSubscriptionWorkspace();
            } catch (e) {
                showToast("无法连接后端接口", "error");
            }
        }

        async function toggleSubscriptionLink(linkId, enabled) {
            try {
                const res = await fetch("./api/panel/subscription-links/toggle", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ id: linkId, enabled })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    showToast(data.error || "更新订阅链接状态失败", "error");
                    return;
                }
                await loadSubscriptionWorkspace();
            } catch (e) {
                showToast("无法连接后端接口", "error");
            }
        }

        async function copySubscriptionUrl(linkId) {
            const link = subscriptionLinks.find(item => item.id === linkId);
            const url = subscriptionUrl(link);
            if (!url) {
                showToast("订阅链接不可用", "warning");
                return;
            }
            try {
                await copyToClipboard(url);
                showToast("订阅链接已复制", "success");
            } catch (e) {
                showToast("复制失败，请重试", "error");
            }
        }

        function copySelectedSubscriptionUrl() {
            const link = selectedSubscriptionLink();
            if (!link) {
                showToast("请先选择订阅链接", "warning");
                return;
            }
            copySubscriptionUrl(link.id);
        }

        async function copySubscriptionNodeUrl(nodeId) {
            try {
                const res = await fetch("./api/panel/subscription-nodes/share-link", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ id: nodeId })
                });
                const data = await res.json();
                if (!res.ok || !data.ok || !data.node || !data.node.link) {
                    showToast(data.error || "节点链接生成失败", "error");
                    return;
                }
                try {
                    await copyToClipboard(data.node.link);
                    showToast("节点链接已复制", "success");
                } catch (e) {
                    showToast("复制失败，请重试", "error");
                }
            } catch (e) {
                showToast("无法连接后端接口", "error");
            }
        }

        function renderSubscriptionSelectOptions(selectedId = "") {
            const select = $("subscription_node_subscription_id");
            if (!select) return;
            if (!subscriptionLinks.length) {
                select.innerHTML = `<option value="">保存时自动创建默认订阅</option>`;
                select.value = "";
                return;
            }
            select.innerHTML = subscriptionLinks.map(link => `<option value="${esc(link.id)}">${esc(link.name || link.id)}</option>`).join("");
            const nextSelected = selectedId || selectedSubscriptionLinkId || subscriptionLinks[0].id;
            select.value = subscriptionLinks.some(link => link.id === nextSelected) ? nextSelected : subscriptionLinks[0].id;
        }

        function updateSubscriptionNodeJoinHint() {
            const checkbox = $("subscription_node_join_subscription");
            const select = $("subscription_node_subscription_id");
            const hint = $("subscription_node_subscription_hint");
            const title = $("subscription_node_modal_title");
            if (!checkbox || !select) return;

            let hintText = "";
            if (!checkbox.checked) {
                hintText = "取消勾选后只创建独立节点链接，不进入任何订阅链接。";
                if (title && !$("subscription_node_id").value) title.textContent = "新建独立节点";
            } else {
                const link = subscriptionLinks.find(item => item.id === select.value);
                if (link) {
                    $("subscription_node_protocol").value = link.protocol || "vless-reality";
                    $("subscription_node_port").value = link.port || "";
                    $("subscription_node_camouflage").value = link.protocol === "socks5" ? "" : (link.camouflage_host || "");
                    handleSubscriptionProtocolChange(link.protocol || "vless-reality");
                    hintText = `保存后会加入“${link.name || link.id}”订阅链接；节点本身仍是独立记录，可单独启停、编辑和删除。`;
                    if (title && !$("subscription_node_id").value) title.textContent = `给“${link.name || link.id}”新建节点`;
                } else {
                    hintText = "当前还没有订阅链接，保存时会自动创建默认订阅并把此节点加入进去。";
                    if (title && !$("subscription_node_id").value) title.textContent = "新建节点";
                }
            }
            if (hint) {
                hint.textContent = hintText;
            }
        }

        function handleSubscriptionJoinChange(checked) {
            const select = $("subscription_node_subscription_id");
            if (!select) return;
            const portGroup = $("subscription_node_port_group");
            const protocolGroup = $("subscription_node_protocol_group");
            const portInput = $("subscription_node_port");
            select.disabled = !checked;
            select.style.opacity = checked ? "1" : ".55";
            if (portGroup) portGroup.style.display = checked ? "none" : "";
            if (protocolGroup) protocolGroup.style.display = checked ? "none" : "";
            if (portInput) portInput.required = !checked;
            if (!checked && !portInput.value) generateSubscriptionPort();
            updateSubscriptionNodeJoinHint();
        }

        function openSubscriptionNodeModal(nodeId = "", preferredLinkId = "", preferredProtocol = "") {
            const node = subscriptionNodes.find(item => item.id === nodeId) || null;
            const linkId = node ? (node.subscription_id || "") : (preferredLinkId || selectedSubscriptionLinkId);
            const joinSubscription = node ? Boolean(node.subscription_id) : true;
            
            const parentLink = subscriptionLinks.find(l => l.id === linkId) || null;
            const protocol = parentLink ? (parentLink.protocol || "vless-reality") : (node ? (node.protocol || "vless-reality") : (preferredProtocol || "vless-reality"));
            
            const modalTitle = $("subscription_node_modal_title");
            $("subscription_node_id").value = node ? node.id : "";
            $("subscription_node_join_subscription").checked = joinSubscription;
            renderSubscriptionSelectOptions(linkId);
            handleSubscriptionJoinChange(joinSubscription);
            $("subscription_node_name").value = node ? (node.name || "") : (typeof nextNodeName === "function" ? nextNodeName(joinSubscription ? "SUB" : "NODE") : "");
            $("subscription_node_protocol").value = protocol;
            $("subscription_node_port").value = parentLink ? (parentLink.port || "") : (node ? (node.port || "") : "");
            $("subscription_node_camouflage").value = protocol === "socks5" ? "" : (parentLink ? (parentLink.camouflage_host || "") : (node ? (node.camouflage_host || "") : ""));
            $("subscription_node_uuid").value = protocol === "socks5" ? "" : (node ? (node.uuid || "") : "");
            $("subscription_node_socks_username").value = node ? (node.socks_username || node.username || (protocol === "socks5" ? (node.uuid || "") : "")) : "";
            $("subscription_node_socks_password").value = node ? (node.socks_password || node.password || "") : "";
            $("subscription_node_error").style.display = "none";
            $("subscription_node_success").style.display = "none";
            if (!node) {
                if (protocol === "socks5") {
                    generateSubscriptionSocksCredential("username");
                    generateSubscriptionSocksCredential("password");
                } else {
                    generateSubscriptionUuid();
                }
            }
            if (modalTitle && node) modalTitle.textContent = node.subscription_id ? "编辑节点链接" : "编辑独立节点";
            const displayEl = $("subscription_node_protocol_display");
            if (displayEl) {
                displayEl.textContent = subscriptionProtocolNames[protocol] || protocol;
            }
            handleSubscriptionProtocolChange(protocol);
            updateSubscriptionNodeJoinHint();
            const modal = $("subscription-node-modal");
            if (modal) modal.style.display = "flex";
        }

        function closeSubscriptionNodeModal() {
            const modal = $("subscription-node-modal");
            if (modal) modal.style.display = "none";
        }

        function editSubscriptionNode(nodeId) {
            openSubscriptionNodeModal(nodeId);
        }

        function generateSubscriptionUuid() {
            const input = $("subscription_node_uuid");
            if (!input) return;
            input.value = generateRandomUUID();
        }

        function generateSubscriptionPort() {
            const input = $("subscription_node_port");
            if (!input) return;
            const used = new Set(subscriptionNodes.map(item => Number(item.port)).filter(Boolean));
            let port = 10000 + Math.floor(Math.random() * 50000);
            for (let i = 0; i < 40 && used.has(port); i++) {
                port = 10000 + Math.floor(Math.random() * 50000);
            }
            input.value = port;
        }

        function generateSubscriptionCamouflage() {
            const input = $("subscription_node_camouflage");
            if (!input) return;
            input.value = camouflageHosts[Math.floor(Math.random() * camouflageHosts.length)];
        }

        function randomCredential(prefix = "") {
            const raw = generateRandomUUID().replaceAll("-", "");
            return `${prefix}${raw.slice(0, 12)}`;
        }

        function generateSubscriptionSocksCredential(kind) {
            const input = kind === "password" ? $("subscription_node_socks_password") : $("subscription_node_socks_username");
            if (!input) return;
            input.value = randomCredential(kind === "password" ? "pwd_" : "user_");
        }

        function handleSubscriptionProtocolChange(protocol) {
            const isSocks = protocol === "socks5";
            const camouflageGroup = $("subscription_node_camouflage_group");
            const uuidGroup = $("subscription_node_uuid_group");
            const socksUsernameGroup = $("subscription_node_socks_username_group");
            const socksPasswordGroup = $("subscription_node_socks_password_group");
            const camouflageInput = $("subscription_node_camouflage");
            const uuidInput = $("subscription_node_uuid");
            const socksUsernameInput = $("subscription_node_socks_username");
            const socksPasswordInput = $("subscription_node_socks_password");

            if (camouflageGroup) camouflageGroup.style.display = isSocks ? "none" : "";
            if (uuidGroup) uuidGroup.style.display = isSocks ? "none" : "";
            if (socksUsernameGroup) socksUsernameGroup.style.display = isSocks ? "" : "none";
            if (socksPasswordGroup) socksPasswordGroup.style.display = isSocks ? "" : "none";

            if (camouflageInput) camouflageInput.required = !isSocks;
            if (uuidInput) uuidInput.required = !isSocks;
            if (socksUsernameInput) socksUsernameInput.required = isSocks;
            if (socksPasswordInput) socksPasswordInput.required = isSocks;

            if (isSocks) {
                if (camouflageInput) camouflageInput.value = "";
                if (uuidInput) uuidInput.value = "";
                if (socksUsernameInput && !socksUsernameInput.value.trim()) generateSubscriptionSocksCredential("username");
                if (socksPasswordInput && !socksPasswordInput.value.trim()) generateSubscriptionSocksCredential("password");
            } else {
                if (socksUsernameInput) socksUsernameInput.value = "";
                if (socksPasswordInput) socksPasswordInput.value = "";
                if (uuidInput && !uuidInput.value.trim()) generateSubscriptionUuid();
                if (camouflageInput && !camouflageInput.value.trim()) generateSubscriptionCamouflage();
            }
        }

        async function saveSubscriptionNode(event) {
            event.preventDefault();
            const err = $("subscription_node_error");
            const ok = $("subscription_node_success");
            const btn = $("subscription_node_submit");
            err.style.display = "none";
            ok.style.display = "none";
            const existing = subscriptionNodes.find(item => item.id === $("subscription_node_id").value);
            const joinSubscription = $("subscription_node_join_subscription").checked;
            const protocol = $("subscription_node_protocol").value;
            const isSocks = protocol === "socks5";
            const payload = {
                id: $("subscription_node_id").value,
                subscription_id: joinSubscription ? $("subscription_node_subscription_id").value : "",
                add_to_subscription: joinSubscription,
                name: $("subscription_node_name").value.trim(),
                protocol,
                port: $("subscription_node_port").value,
                uuid: isSocks ? "" : $("subscription_node_uuid").value.trim(),
                camouflage_host: isSocks ? "" : $("subscription_node_camouflage").value.trim(),
                socks_username: isSocks ? $("subscription_node_socks_username").value.trim() : "",
                socks_password: isSocks ? $("subscription_node_socks_password").value.trim() : "",
                enabled: existing ? existing.enabled === true : true,
                outbound_node_id: existing ? (existing.outbound_node_id || "") : "",
                created_at: existing ? existing.created_at : ""
            };
            btn.disabled = true;
            btn.textContent = "保存中...";
            try {
                const res = await fetch("./api/panel/subscription-nodes", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    err.textContent = data.error || "保存订阅节点失败";
                    err.style.display = "block";
                    return;
                }
                ok.textContent = data.message || "订阅节点已保存";
                ok.style.display = "block";
                await loadSubscriptionWorkspace();
                setTimeout(closeSubscriptionNodeModal, 450);
            } catch (e) {
                err.textContent = "无法连接后端接口";
                err.style.display = "block";
            } finally {
                btn.disabled = false;
                btn.textContent = "创建节点";
            }
        }

        async function deleteSubscriptionNode(nodeId) {
            if (!confirm("确定删除这个订阅节点吗？")) return;
            try {
                const res = await fetch("./api/panel/subscription-nodes/delete", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ id: nodeId })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    alert(data.error || "删除订阅节点失败");
                    return;
                }
                await loadSubscriptionWorkspace();
            } catch (e) {
                alert("无法连接后端接口");
            }
        }

        async function toggleSubscriptionNode(nodeId, enabled) {
            try {
                const res = await fetch("./api/panel/subscription-nodes/toggle", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ id: nodeId, enabled })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    alert(data.error || "更新订阅节点状态失败");
                    return;
                }
                await loadSubscriptionWorkspace();
            } catch (e) {
                alert("无法连接后端接口");
            }
        }

        function addClientToInbound(ibIdx) {
            syncXrayInboundFromDom();
            const ib = xrayConfig.inbounds[ibIdx];
            if (!ib.clients) ib.clients = [];
            const rand = generateRandomUUID();
            const newClient = {
                name: "client-" + (ib.clients.length + 1).toString().padStart(2, "0"),
                uuid: ib.protocol === "vless" || ib.protocol === "vmess" ? rand : "",
                password: ib.protocol === "vless" || ib.protocol === "vmess" ? "" : rand.replace(/-/g, "").slice(0, 16),
                status: "active"
            };
            ib.clients.push(newClient);
            renderXrayInbounds();
        }

        function removeClientFromInbound(ibIdx, clientIdx) {
            syncXrayInboundFromDom();
            const ib = xrayConfig.inbounds[ibIdx];
            if (!ib.clients || ib.clients.length <= 1) {
                alert("每个入站协议必须至少保留一个客户端账户。");
                return;
            }
            ib.clients.splice(clientIdx, 1);
            renderXrayInbounds();
        }

        function generateRandomClientSecret(ibIdx, clientIdx) {
            syncXrayInboundFromDom();
            const ib = xrayConfig.inbounds[ibIdx];
            const client = ib.clients[clientIdx];
            const rand = generateRandomUUID();
            if (ib.protocol === "vless" || ib.protocol === "vmess") {
                client.uuid = rand;
            } else {
                client.password = rand.replace(/-/g, "").slice(0, 16);
            }
            renderXrayInbounds();
        }

        let xrayConfig = null;

        function defaultXrayInbound() {
            const rand = generateRandomUUID();
            return {
                id: "inbound-" + Date.now(),
                protocol: "vless",
                listen: "0.0.0.0",
                port: 10086,
                uuid: rand,
                password: "",
                network: "tcp",
                encryption: "none",
                ws_path: "/",
                remark: "VLESS 入站"
            };
        }

        function xraySecretValue(ib) {
            return ib.protocol === "vless" || ib.protocol === "vmess" ? (ib.uuid || "") : (ib.password || "");
        }

        async function resetClientTraffic(name) {
            if (!confirm(`确定要重置客户端 ${name} 的已用流量吗？
重置后如果该客户端当前处于禁用状态，系统将自动重新启用。`)) return;
            try {
                const response = await fetch("./api/xray/reset_client_traffic", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({ name })
                });
                const res = await response.json();
                if (res.ok) {
                    fetchStats();
                    const cfgRes = await fetch("./api/xray/config");
                    xrayConfig = await cfgRes.json();
                    renderXrayInbounds();
                    refreshXrayStatus();
                } else {
                    alert("重置流量失败: " + (res.error || "未知错误"));
                }
            } catch (e) {
                alert("请求失败: " + e);
            }
        }

        function renderXrayInbounds() {
            const container = $("xray_inbounds");
            if (!container || !xrayConfig) return;
            const inbounds = xrayConfig.inbounds || [];

            inbounds.forEach((ib, ibIdx) => {
                if (!ib.clients || !Array.isArray(ib.clients)) {
                    const secret = ib.uuid || ib.password || "";
                    if (ib.protocol === "vless" || ib.protocol === "vmess") {
                        ib.clients = [{ name: "client-01", uuid: secret, password: "", status: "active" }];
                    } else {
                        ib.clients = [{ name: "client-01", uuid: "", password: secret, status: "active" }];
                    }
                }
            });

            container.innerHTML = inbounds.map((ib, ibIdx) => {
                const isShadowsocks = ib.protocol === "shadowsocks";
                let clientsHtml = "";
                if (isShadowsocks) {
                    clientsHtml = `
                        <div class="mb-[22px] text-left" style="margin-top:12px;">
                            <label style="font-size:12px; font-weight:600; color:var(--muted); text-transform:uppercase;">Shadowsocks 单密码密匙</label>
                            <input class="form-input xray-shadowsocks-secret xray-secret-field" value="${esc(ib.password || ib.uuid || "defaultpassword")}" style="height:36px; margin-top:6px;">
                        </div>
                    `;
                } else {
                    clientsHtml = `
                        <div class="clients-section">
                            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 12px;">
                                <strong style="font-size: 13.5px; color: var(--text);">客户端账户授权列表 (${ib.protocol.toUpperCase()})</strong>
                                <button type="button" class="btn btn-secondary btn-sm" onclick="addClientToInbound(${ibIdx})" style="height:28px; width:auto; margin-bottom:0;">新增客户端</button>
                            </div>
                            <div style="border: 1px solid var(--border); border-radius: 8px; padding: 12px; background: rgba(0,0,0,0.02); overflow-x:auto;">
                                <div class="client-header-row">
                                    <div style="text-align:left;">账户备注</div>
                                    <div style="text-align:left;">UUID / 密钥</div>
                                    <div>限额 (GB)</div>
                                    <div>到期时间</div>
                                    <div>已用上行</div>
                                    <div>已用下行</div>
                                    <div>状态</div>
                                    <div>操作</div>
                                </div>
                                ${ib.clients.map((client, clientIdx) => {
                        const clientSecret = ib.protocol === "vless" || ib.protocol === "vmess" ? (client.uuid || "") : (client.password || "");
                        const uploaded = client.uploaded || 0;
                        const downloaded = client.downloaded || 0;
                        return `
                                        <div class="client-row" data-client-index="${clientIdx}" data-client-name="${esc(client.name)}">
                                            <div class="mb-[22px] text-left">
                                                <input type="text" placeholder="账户备注" class="form-input client-name" value="${esc(client.name)}" style="height:32px; font-size:12.5px;">
                                            </div>
                                            <div class="mb-[22px] text-left" style="display:flex; gap:4px; align-items:center;">
                                                <input type="text" placeholder="UUID / 密匙" class="form-input client-secret xray-secret-field" value="${esc(clientSecret)}" style="height:32px; font-size:11px; flex:1;">
                                                <button type="button" class="btn btn-secondary btn-sm" onclick="generateRandomClientSecret(${ibIdx}, ${clientIdx})" style="height:32px; padding:0 8px; width:auto; font-size:11px; margin-bottom:0;">生成</button>
                                            </div>
                                            <div class="mb-[22px] text-left">
                                                <input type="number" min="0" step="0.1" placeholder="无限制" class="form-input client-quota" value="${client.quota_gb || 0}" style="height:32px; font-size:12.5px; padding:0 4px; text-align:center;" title="流量限制 (GB)，0 表示无限制">
                                            </div>
                                            <div class="mb-[22px] text-left">
                                                <input type="date" class="form-input client-expiry" value="${formatDatePickerDate(client.expiry_time)}" style="height:32px; font-size:11.5px; padding:0 4px;" title="过期时间，为空表示永不过期">
                                            </div>
                                            <div class="client-traffic-stat" style="font-size:11.5px; color:var(--muted); text-align:center; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" id="traffic-up-${esc(client.name)}">
                                                ↑ ${formatBytes(uploaded)}
                                            </div>
                                            <div class="client-traffic-stat" style="font-size:11.5px; color:var(--muted); text-align:center; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" id="traffic-down-${esc(client.name)}">
                                                ↓ ${formatBytes(downloaded)}
                                            </div>
                                            <input type="hidden" class="client-uploaded" value="${uploaded}">
                                            <input type="hidden" class="client-downloaded" value="${downloaded}">
                                            <div class="mb-[22px] text-left">
                                                <select class="form-input client-status" style="height:32px; font-size:12.5px; padding:0 4px;">
                                                    <option value="active" ${client.status === "active" ? "selected" : ""}>启用</option>
                                                    <option value="disabled" ${client.status === "disabled" ? "selected" : ""}>禁用</option>
                                                </select>
                                            </div>
                                            <div style="display:flex; gap:4px; justify-content:center;">
                                                <button type="button" class="btn btn-secondary btn-sm" onclick="openShareModal(${ibIdx}, ${clientIdx})" style="height:32px; padding:0 8px; width:auto; font-size:11px; margin-bottom:0;" title="分享节点与订阅">分享</button>
                                                <button type="button" class="btn btn-secondary btn-sm" onclick="${esc(`resetClientTraffic(${jsArg(client.name)})`)}" style="height:32px; padding:0 8px; width:auto; font-size:11px; margin-bottom:0;" title="重置已用流量">重置</button>
                                                <button type="button" class="btn btn-danger btn-sm" onclick="removeClientFromInbound(${ibIdx}, ${clientIdx})" style="height:32px; padding:0 8px; width:auto; font-size:11px; margin-bottom:0;">删除</button>
                                            </div>
                                        </div>
                                    `;
                    }).join("")}
                            </div>
                        </div>
                    `;
                }

                return `
                    <div class="xray-inbound-card" data-index="${ibIdx}">
                        <div class="xray-inbound-card-header">
                            <h4 style="font-size:14.5px; font-weight:700; color:var(--text);">${esc(ib.remark || ib.protocol.toUpperCase() + " 入站")}</h4>
                            <button type="button" class="w-full min-h-[32px] py-2 px-4 bg-gradient-to-br from-[#ff5370] to-danger border-none rounded-xl text-white font-bold cursor-pointer shadow-[0_12px_24px_rgba(255,83,112,0.24)] transition-all duration-[280ms] ease inline-flex justify-center items-center gap-2 hover:translate-y-[-2px] hover:shadow-[0_14px_28px_rgba(255,83,112,0.32)] active:translate-y-0 disabled:opacity-60 disabled:cursor-not-allowed" onclick="removeXrayInbound(${ibIdx})" style="width:auto; height:28px; padding:0 12px; font-size:12px; margin-bottom: 0; border-radius:6px;">删除入站</button>
                        </div>
                        <div class="xray-inbound-card-grid">
                            <div class="mb-[22px] text-left">
                                <label>入站协议</label>
                                <select class="form-input xray-protocol" onchange="syncXrayInboundFromDom(); renderXrayInbounds();" style="height:36px; padding:0 8px;">
                                    ${["vless", "vmess", "trojan", "shadowsocks"].map(p => `<option value="${p}" ${ib.protocol === p ? "selected" : ""}>${p.toUpperCase()}</option>`).join("")}
                                </select>
                            </div>
                            <div class="mb-[22px] text-left">
                                <label>监听端口</label>
                                <input type="number" min="1" max="65535" class="form-input xray-port" value="${esc(ib.port || 10086)}" style="height:36px;">
                            </div>
                            <div class="mb-[22px] text-left">
                                <label>传输网络</label>
                                <select class="form-input xray-network" onchange="syncXrayInboundFromDom(); renderXrayInbounds();" style="height:36px; padding:0 8px;">
                                    <option value="tcp" ${ib.network === "tcp" ? "selected" : ""}>TCP</option>
                                    <option value="ws" ${ib.network === "ws" ? "selected" : ""}>WebSocket</option>
                                </select>
                            </div>
                            <div class="mb-[22px] text-left">
                                <label>WS 路径 (仅ws有效)</label>
                                <input class="form-input xray-ws-path" value="${esc(ib.ws_path || "/")}" ${ib.network === "ws" ? "" : "disabled"} style="height:36px;">
                            </div>
                            <input type="hidden" class="xray-listen" value="${esc(ib.listen || "0.0.0.0")}">
                            <input type="hidden" class="xray-remark" value="${esc(ib.remark || "")}">
                        </div>
                        ${clientsHtml}
                    </div>
                `;
            }).join("");
        }

        function syncXrayInboundFromDom() {
            if (!xrayConfig) return;
            const cards = document.querySelectorAll(".xray-inbound-card");
            xrayConfig.inbounds = Array.from(cards).map((card, ibIdx) => {
                const protocol = card.querySelector(".xray-protocol").value;
                const port = parseInt(card.querySelector(".xray-port").value, 10);
                const network = card.querySelector(".xray-network").value;
                const ws_path = card.querySelector(".xray-ws-path").value.trim() || "/";
                const listen = card.querySelector(".xray-listen").value || "0.0.0.0";
                const remark = card.querySelector(".xray-remark").value || `${protocol.toUpperCase()} 入站`;

                let clients = [];
                if (protocol === "shadowsocks") {
                    const secretInput = card.querySelector(".xray-shadowsocks-secret");
                    const secret = secretInput ? secretInput.value.trim() : "defaultpassword";
                    clients = [{ name: "client-01", uuid: "", password: secret, status: "active" }];
                } else {
                    const clientRows = card.querySelectorAll(".client-row");
                    clients = Array.from(clientRows).map((row, clientIdx) => {
                        const name = row.querySelector(".client-name").value.trim();
                        const secret = row.querySelector(".client-secret").value.trim();
                        const status = row.querySelector(".client-status").value;
                        const quota_gb = parseFloat(row.querySelector(".client-quota").value) || 0.0;
                        const expiryVal = row.querySelector(".client-expiry").value;
                        const expiry_time = expiryVal ? Math.floor(new Date(expiryVal).getTime() / 1000) : 0;
                        const uploaded = parseInt(row.querySelector(".client-uploaded")?.value || "0", 10);
                        const downloaded = parseInt(row.querySelector(".client-downloaded")?.value || "0", 10);

                        const c = {
                            name,
                            status,
                            quota_gb,
                            expiry_time,
                            uploaded,
                            downloaded,
                            uuid: protocol === "vless" || protocol === "vmess" ? secret : "",
                            password: protocol === "vless" || protocol === "vmess" ? "" : secret
                        };
                        return c;
                    });
                }

                if (!clients.length) {
                    clients = [{ name: "client-01", uuid: "", password: "defaultpassword", status: "active" }];
                }

                const first_client = clients[0];
                const item = {
                    id: xrayConfig.inbounds[ibIdx]?.id || ("inbound-" + ibIdx),
                    protocol,
                    listen,
                    port,
                    network,
                    encryption: protocol === "vless" ? "none" : "aes-256-gcm",
                    ws_path,
                    remark,
                    uuid: first_client.uuid,
                    password: first_client.password,
                    clients
                };
                return item;
            });
        }

        function addXrayInbound() {
            syncXrayInboundFromDom();
            if (!xrayConfig) xrayConfig = { enabled: true, require_vpn: false, outbound_interface: "tun0", loglevel: "warning", inbounds: [] };
            xrayConfig.inbounds.push(defaultXrayInbound());
            renderXrayInbounds();
        }

        function removeXrayInbound(idx) {
            syncXrayInboundFromDom();
            if (!xrayConfig || xrayConfig.inbounds.length <= 1) {
                alert("至少保留一个入站配置。");
                return;
            }
            xrayConfig.inbounds.splice(idx, 1);
            renderXrayInbounds();
        }

        async function loadXrayPanel() {
            try {
                const [cfgRes, statusRes] = await Promise.all([
                    fetch("./api/xray/config"),
                    fetch("./api/xray/status")
                ]);
                xrayConfig = await cfgRes.json();
                const status = await statusRes.json();
                const enabledRadio = document.querySelector(`input[name="xray_enabled"][value="${xrayConfig.enabled ? "true" : "false"}"]`);
                if (enabledRadio) enabledRadio.checked = true;
                if ($("xray_outbound_interface")) $("xray_outbound_interface").value = xrayConfig.outbound_interface || "tun0";
                if ($("xray_loglevel")) $("xray_loglevel").value = xrayConfig.loglevel || "warning";
                if ($("xray_require_vpn")) $("xray_require_vpn").checked = xrayConfig.require_vpn === true;
                renderXrayInbounds();
                renderXrayStatus(status);
            } catch (e) {
                const errDiv = $("xray_form_error") || $("xray_error_box");
                if (errDiv) {
                    errDiv.textContent = "加载 Xray 配置失败，请检查后端接口。";
                    errDiv.style.display = "block";
                }
            }
        }

        async function saveXrayConfig(e) {
            e.preventDefault();
            const errDiv = $("xray_form_error");
            const okDiv = $("xray_form_success");
            errDiv.style.display = "none";
            okDiv.style.display = "none";
            syncXrayInboundFromDom();
            const enabledRadio = document.querySelector('input[name="xray_enabled"]:checked');
            xrayConfig.enabled = enabledRadio ? enabledRadio.value === "true" : false;
            xrayConfig.outbound_interface = $("xray_outbound_interface").value.trim() || "tun0";
            xrayConfig.loglevel = $("xray_loglevel").value;
            xrayConfig.require_vpn = $("xray_require_vpn").checked;

            if (!xrayConfig.inbounds.length) {
                errDiv.textContent = "至少需要一个 Xray 入站配置。";
                errDiv.style.display = "block";
                return;
            }
            for (const ib of xrayConfig.inbounds) {
                if (!ib.port || ib.port < 1 || ib.port > 65535) {
                    errDiv.textContent = "入站端口必须在 1 至 65535 之间。";
                    errDiv.style.display = "block";
                    return;
                }
                for (const c of ib.clients) {
                    const val = ib.protocol === "vless" || ib.protocol === "vmess" ? c.uuid : c.password;
                    if (!val || !val.trim()) {
                        errDiv.textContent = `在入站端口 ${ib.port} 下的客户端密码/UUID不能为空。`;
                        errDiv.style.display = "block";
                        return;
                    }
                }
            }

            const btn = $("xray_save_btn");
            btn.disabled = true;
            btn.textContent = "正在保存...";
            try {
                const res = await fetch("./api/xray/config", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(xrayConfig)
                });
                const data = await res.json();
                if (res.ok && data.ok) {
                    okDiv.textContent = data.message || "Xray 配置保存成功并已重载服务。";
                    okDiv.style.display = "block";
                    setTimeout(() => {
                        okDiv.style.display = "none";
                        loadXrayPanel();
                    }, 2000);
                } else {
                    errDiv.textContent = data.error || "保存 Xray 配置失败。";
                    errDiv.style.display = "block";
                }
            } catch (err) {
                errDiv.textContent = "保存 Xray 配置时无法连接后端。";
                errDiv.style.display = "block";
            } finally {
                btn.disabled = false;
                btn.textContent = "保存入站配置";
            }
        }

        async function saveXraySettingsOnly(e) {
            if (e) e.preventDefault();
            const errDiv = $("xray_settings_error");
            const okDiv = $("xray_settings_success");
            errDiv.style.display = "none";
            okDiv.style.display = "none";

            const enabledRadio = document.querySelector('input[name="xray_enabled"]:checked');
            xrayConfig.enabled = enabledRadio ? enabledRadio.value === "true" : false;
            xrayConfig.outbound_interface = $("xray_outbound_interface").value.trim() || "tun0";
            xrayConfig.loglevel = $("xray_loglevel").value;
            xrayConfig.require_vpn = $("xray_require_vpn").checked;

            const btn = $("xray_settings_save_btn");
            btn.disabled = true;
            btn.textContent = "正在保存...";
            try {
                const res = await fetch("./api/xray/config", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(xrayConfig)
                });
                const data = await res.json();
                if (res.ok && data.ok) {
                    okDiv.textContent = data.message || "Xray 服务设置保存成功并已重载服务。";
                    okDiv.style.display = "block";
                    setTimeout(() => {
                        okDiv.style.display = "none";
                        loadXrayPanel();
                    }, 2000);
                } else {
                    errDiv.textContent = data.error || "保存 Xray 服务设置失败。";
                    errDiv.style.display = "block";
                }
            } catch (err) {
                errDiv.textContent = "保存 Xray 服务设置时无法连接后端。";
                errDiv.style.display = "block";
            } finally {
                btn.disabled = false;
                btn.textContent = "保存服务设置";
            }
        }
