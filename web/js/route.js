
        let routingRules = [];
        async function loadRoutingRules() {
            try {
                const [rulesRes, subLinksRes, subNodesRes, outNodesRes] = await Promise.all([
                    fetch("./api/panel/routing-rules"),
                    fetch("./api/panel/subscription-links"),
                    fetch("./api/panel/subscription-nodes"),
                    fetch("./api/panel/outbound-nodes")
                ]);
                const rulesData = await rulesRes.json();
                const subLinksData = await subLinksRes.json();
                const subData = await subNodesRes.json();
                const outData = await outNodesRes.json();
                routingRules = Array.isArray(rulesData.rules) ? rulesData.rules : [];
                subscriptionLinks = Array.isArray(subLinksData.subscriptions) ? subLinksData.subscriptions : subscriptionLinks;
                subscriptionNodes = Array.isArray(subData.nodes) ? subData.nodes : subscriptionNodes;
                outboundNodes = Array.isArray(outData.nodes) ? outData.nodes : [];
                renderRoutingRules();
            } catch (e) {
                const tbody = $("routing_rules_rows");
                if (tbody) tbody.innerHTML = `<tr><td colspan="6" class="compact-empty">路由规则加载失败</td></tr>`;
            }
        }

        function outboundOptions() {
            const merged = [...virtualOutboundNodes, ...outboundNodes.filter(item => item.enabled !== false)];
            const seen = new Set();
            return merged.filter(item => {
                const id = item.id || item.tag || "";
                if (!id || seen.has(id)) return false;
                seen.add(id);
                return true;
            }).map(item => ({
                id: item.id || item.tag,
                name: item.name || item.tag || item.id,
                type: item.type || ""
            }));
        }

        function nodeNameById(id) {
            const link = subscriptionLinks.find(item => item.id === id);
            if (link) return `${link.name} (订阅)`;
            const node = subscriptionNodes.find(item => item.id === id);
            if (!node) return id || "-";
            const parentLink = subscriptionLinks.find(item => item.id === node.subscription_id);
            const linkName = parentLink ? parentLink.name : "";
            return linkName ? `${linkName} / ${node.name}` : node.name;
        }

        function inboundOptionsForRule(rule) {
            const options = [];
            subscriptionLinks.forEach(link => {
                options.push({
                    id: link.id,
                    name: link.name || link.id,
                    detail: subscriptionProtocolNames[link.protocol] || link.protocol || "订阅",
                    kind: "订阅"
                });
            });
            subscriptionNodes.forEach(node => {
                options.push({
                    id: node.id,
                    name: node.name || node.id,
                    detail: `${subscriptionProtocolNames[node.protocol] || node.protocol || "节点"} · 端口 ${node.port || "-"}`,
                    kind: node.subscription_id ? "节点" : "独立节点"
                });
            });
            const seen = new Set();
            return options.filter(item => {
                if (!item.id || seen.has(item.id)) return false;
                seen.add(item.id);
                return true;
            });
        }

        function asArray(value) {
            if (Array.isArray(value)) return value.filter(Boolean);
            return value ? [value] : [];
        }

        function selectedValues(selectId) {
            const select = $(selectId);
            if (!select) return [];
            return Array.from(select.selectedOptions).map(option => option.value).filter(Boolean);
        }

        function namesByIds(ids, resolver) {
            const values = asArray(ids);
            if (!values.length) return "-";
            return values.map(resolver).join("、");
        }

        function outboundNameById(id) {
            const node = outboundOptions().find(item => item.id === id);
            return node ? node.name : (id || "-");
        }

        function routingConditionLabel(type) {
            const labels = { all: "全部流量", domain: "指定网站", ip: "指定 IP", port: "指定端口" };
            return labels[type] || type || "-";
        }

        function routingConditions(rule) {
            if (Array.isArray(rule.match_conditions) && rule.match_conditions.length) {
                return rule.match_conditions;
            }
            return [{ type: rule.match_type || "all", value: rule.match_value || "" }];
        }

        function formatRoutingConditions(rule) {
            return routingConditions(rule).map(item => {
                if (item.type === "all") return "全部流量";
                return `${routingConditionLabel(item.type)}: ${item.value || "-"}`;
            }).join("、");
        }

        function splitInputValues(value) {
            return String(value || "").split(/[\n,，;；]+/).map(item => item.trim()).filter(Boolean);
        }

        function updateRoutingStatusHint() {
            const domainVal = $("routing_match_domain_values").value.trim();
            const ipVal = $("routing_match_ip_values").value.trim();
            const portVal = $("routing_match_port_values").value.trim();
            const hintEl = $("routing_rule_match_hint");
            if (!hintEl) return;
            
            if (!domainVal && !ipVal && !portVal) {
                hintEl.innerHTML = `<span class="text-success font-bold">⚡ 全部流量</span>`;
            } else {
                hintEl.innerHTML = `<span class="text-primary font-bold">🔍 分流匹配</span>`;
            }
        }

        function collectRoutingConditions() {
            const conditions = [];
            const domainVal = $("routing_match_domain_values").value.trim();
            const ipVal = $("routing_match_ip_values").value.trim();
            const portVal = $("routing_match_port_values").value.trim();
            
            if (!domainVal && !ipVal && !portVal) {
                conditions.push({ type: "all", value: "" });
                return conditions;
            }
            
            if (domainVal) {
                splitInputValues(domainVal).forEach(val => conditions.push({ type: "domain", value: val }));
            }
            if (ipVal) {
                splitInputValues(ipVal).forEach(val => conditions.push({ type: "ip", value: val }));
            }
            if (portVal) {
                splitInputValues(portVal).forEach(val => conditions.push({ type: "port", value: val }));
            }
            return conditions;
        }

        function renderRoutingRules() {
            const container = $("routing_rules_rows");
            if (!container) return;
            if (!routingRules.length) {
                container.innerHTML = `<div class="outbound-state-panel">暂无路由规则</div>`;
                return;
            }
            container.innerHTML = routingRules.map(rule => {
                const enabled = rule.enabled !== false;
                const statusText = enabled ? "已启用" : "未启用";
                const matchText = formatRoutingConditions(rule);
                const inboundIds = rule.inbound_node_ids || rule.inbound_node_id;
                const outboundIds = rule.outbound_node_ids || rule.outbound_node_id;
                const inboundsText = namesByIds(inboundIds, nodeNameById) || "所有入站";
                const outboundsText = namesByIds(outboundIds, outboundNameById) || "系统直连";

                const actionsHtml = [
                    actionButton(enabled ? "停用" : "启用", "power", `toggleRoutingRule(${jsArg(rule.id)}, ${enabled ? "false" : "true"})`, false, false, enabled ? 'text-success' : 'text-muted'),
                    actionButton("编辑", "edit", `editRoutingRule(${jsArg(rule.id)})`),
                    actionButton("删除", "trash", `deleteRoutingRule(${jsArg(rule.id)})`, true)
                ].join("");

                return `
                    <div class="node-card bg-[rgba(255,255,255,0.015)] border border-[color-mix(in_srgb,var(--border)_20%,transparent)] rounded-lg py-2.5 px-4 flex items-center justify-between gap-4 hover:bg-[rgba(255,255,255,0.04)] transition-all duration-200">
                        <div class="flex items-center gap-3.5 min-w-0 flex-1">
                            <span class="w-2.5 h-2.5 rounded-full ${enabled ? 'bg-[var(--success)] shadow-[0_0_6px_var(--success)]' : 'bg-[var(--muted)]'} flex-none" title="${statusText}"></span>
                            <div class="flex flex-col gap-1.5 min-w-0">
                                <div class="flex items-center gap-2">
                                    <strong class="text-[14px] font-semibold text-text truncate max-w-[200px]" title="${esc(rule.name || "-")}">${esc(rule.name || "-")}</strong>
                                    <span class="text-[11px] text-muted ml-2">${esc(rule.status_text || "未写入 Xray")}</span>
                                </div>
                                <div class="flex items-center gap-2 text-[12px] text-muted flex-wrap">
                                    <span class="px-1.5 py-0.5 rounded bg-[rgba(255,255,255,0.06)] border border-[color-mix(in_srgb,var(--border)_20%,transparent)] text-[11px] font-mono leading-none">${esc(inboundsText)}</span>
                                    <span>👉</span>
                                    <span class="px-1.5 py-0.5 rounded bg-[color-mix(in_srgb,var(--primary)_15%,transparent)] border border-[color-mix(in_srgb,var(--primary)_25%,transparent)] text-primary text-[11px] font-mono leading-none">${esc(outboundsText)}</span>
                                    <span class="text-text font-medium ml-1">(${esc(matchText)})</span>
                                </div>
                            </div>
                        </div>
                        <div class="flex items-center gap-1 flex-none">
                            ${actionsHtml}
                        </div>
                    </div>
                `;
            }).join("");
        }

        function availableOutboundOptionsForRule(rule) {
            const merged = [...virtualOutboundNodes, ...outboundNodes];
            const seen = new Set();
            return merged.filter(item => {
                const id = item.id || item.tag || "";
                if (!id || seen.has(id)) return false;
                seen.add(id);
                return true;
            }).map(item => ({
                id: item.id || item.tag,
                name: item.name || item.tag || item.id,
                type: item.type || ""
            }));
        }

        function applyRoutingConditionsToForm(rule) {
            const conditions = rule ? routingConditions(rule) : [];
            const grouped = { domain: [], ip: [], port: [] };
            conditions.forEach(item => {
                if (grouped[item.type] && item.value) grouped[item.type].push(item.value);
            });
            $("routing_match_domain_values").value = grouped.domain.join(", ");
            $("routing_match_ip_values").value = grouped.ip.join(", ");
            $("routing_match_port_values").value = grouped.port.join(", ");
            updateRoutingStatusHint();
        }

        async function openRoutingRuleModal(ruleId = "") {
            if (!subscriptionLinks.length) await loadSubscriptionWorkspace();
            await loadRoutingRules();
            
            const rule = routingRules.find(item => item.id === ruleId) || null;
            $("routing_rule_id").value = rule ? rule.id : "";
            $("routing_rule_name").value = rule ? (rule.name || "") : "";
            
            const inboundContainer = $("routing_rule_inbound_pills");
            const outboundContainer = $("routing_rule_outbound_pills");
            
            const selectedInbounds = new Set(asArray(rule ? (rule.inbound_node_ids || rule.inbound_node_id) : []));
            const selectedOutbounds = new Set(asArray(rule ? (rule.outbound_node_ids || rule.outbound_node_id) : []));
            
            if (inboundContainer) {
                const inboundOptions = inboundOptionsForRule(rule);
                inboundContainer.innerHTML = inboundOptions.length
                    ? inboundOptions.map(item => {
                        const checked = selectedInbounds.has(item.id) || (selectedInbounds.size === 0 && inboundOptions.length === 1) ? "checked" : "";
                        return `
                            <label class="flex items-center gap-2 py-1.5 px-3 bg-glass/40 border border-border rounded-xl cursor-pointer hover:border-primary/60 transition-all select-none">
                                <input type="checkbox" name="routing_inbound" value="${esc(item.id)}" ${checked} class="rounded border-border text-primary focus:ring-primary w-4 h-4">
                                <span class="text-[13px] font-semibold text-text">${esc(item.name)} <span class="text-[11px] text-muted font-normal">(${esc(item.kind)} / ${esc(item.detail)})</span></span>
                            </label>
                        `;
                    }).join("")
                    : `<div class="text-xs text-muted py-2 w-full text-center">请先配置入站订阅</div>`;
            }
            
            if (outboundContainer) {
                const options = availableOutboundOptionsForRule(rule);
                outboundContainer.innerHTML = options.length
                    ? options.map(out => {
                        const checked = selectedOutbounds.has(out.id) || (selectedOutbounds.size === 0 && out.id === "vpn-out") ? "checked" : "";
                        return `
                            <label class="flex items-center gap-2 py-1.5 px-3 bg-glass/40 border border-border rounded-xl cursor-pointer hover:border-primary/60 transition-all select-none">
                                <input type="radio" name="routing_outbound" value="${esc(out.id)}" ${checked} class="rounded-full border-border text-primary focus:ring-primary w-4 h-4">
                                <span class="text-[13px] font-semibold text-text">${esc(out.name)}</span>
                            </label>
                        `;
                    }).join("")
                    : `<div class="text-xs text-muted py-2 w-full text-center">请先创建出站节点</div>`;
            }
            
            applyRoutingConditionsToForm(rule);
            
            $("routing_rule_error").style.display = "none";
            $("routing_rule_success").style.display = "none";
            
            const title = $("routing_rule_modal_title");
            if (title) title.textContent = rule ? "编辑路由规则" : "创建路由规则";
            
            const submit = $("routing_rule_submit");
            if (submit) submit.textContent = rule ? "保存规则" : "创建规则";
                        const saveBtn = $("routing_rule_save_btn");
            if (saveBtn) saveBtn.textContent = rule ? "保存应用" : "保存应用";
            
            const modal = $("routing-rule-modal");
            if (modal) modal.style.display = "flex";
        }

        function editRoutingRule(ruleId) {
            openRoutingRuleModal(ruleId);
        }

        function closeRoutingRuleModal() {
            const modal = $("routing-rule-modal");
            if (modal) modal.style.display = "none";
        }

        async function saveRoutingRule(event, applyImmediately = true) {
            event.preventDefault();
            const err = $("routing_rule_error");
            const ok = $("routing_rule_success");
            const btn = $("routing_rule_save_btn");
            err.style.display = "none";
            ok.style.display = "none";
            
            const inbound_node_ids = Array.from(document.querySelectorAll("input[name='routing_inbound']:checked")).map(el => el.value);
            const outbound_node_ids = Array.from(document.querySelectorAll("input[name='routing_outbound']:checked")).map(el => el.value);
            
            const payload = {
                id: $("routing_rule_id").value,
                name: $("routing_rule_name").value.trim(),
                inbound_node_ids: inbound_node_ids,
                outbound_node_ids: outbound_node_ids,
                match_conditions: collectRoutingConditions(),
                enabled: true,
                apply_immediately: applyImmediately
            };
            
            if (!payload.inbound_node_ids.length) {
                err.textContent = "请至少勾选一个入站服务作为来源";
                err.style.display = "block";
                return;
            }
            if (!payload.outbound_node_ids.length) {
                err.textContent = "请至少勾选一个出站口进行重定向";
                err.style.display = "block";
                return;
            }
            
            const existing = routingRules.find(item => item.id === payload.id);
            if (existing) payload.enabled = existing.enabled !== false;
            
            payload.match_type = payload.match_conditions[0].type;
            payload.match_value = payload.match_conditions[0].value || "";
            
            btn.disabled = true;
            btn.textContent = applyImmediately ? "保存中..." : "创建中...";
            try {
                const res = await fetch("./api/panel/routing-rules", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    err.textContent = data.error || "保存路由规则失败";
                    err.style.display = "block";
                    return;
                }
                ok.textContent = data.message || "路由规则已保存";
                ok.style.display = "block";
                await loadRoutingRules();
                if (applyImmediately) setTimeout(closeRoutingRuleModal, 450);
                if (!applyImmediately && data.rule && data.rule.id) {
                    $("routing_rule_id").value = data.rule.id;
                }
            } catch (e) {
                err.textContent = "无法连接后端接口";
                err.style.display = "block";
            } finally {
                btn.disabled = false;
                btn.textContent = applyImmediately ? "保存应用" : "创建草稿";
            }
        }

        async function deleteRoutingRule(ruleId) {
            if (!confirm("确定删除这个路由规则吗？")) return;
            try {
                const res = await fetch("./api/panel/routing-rules/delete", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ id: ruleId })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    alert(data.error || "删除路由规则失败");
                    return;
                }
                await loadRoutingRules();
            } catch (e) {
                alert("无法连接后端接口");
            }
        }

        async function toggleRoutingRule(ruleId, enabled) {
            try {
                const res = await fetch("./api/panel/routing-rules/toggle", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ id: ruleId, enabled })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    alert(data.error || "更新路由规则状态失败");
                    return;
                }
                await loadRoutingRules();
            } catch (e) {
                alert("无法连接后端接口");
            }
        }

        window.loadRoutingRules = loadRoutingRules;
        window.updateRoutingStatusHint = updateRoutingStatusHint;
        window.openRoutingRuleModal = openRoutingRuleModal;
        window.editRoutingRule = editRoutingRule;
        window.closeRoutingRuleModal = closeRoutingRuleModal;
        window.saveRoutingRule = saveRoutingRule;
        window.deleteRoutingRule = deleteRoutingRule;
        window.toggleRoutingRule = toggleRoutingRule;
