
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
            const tbody = $("routing_rules_rows");
            if (!tbody) return;
            if (!routingRules.length) {
                tbody.innerHTML = `<tr><td colspan="6" class="compact-empty">暂无路由规则</td></tr>`;
                return;
            }
            tbody.innerHTML = routingRules.map(rule => {
                const enabled = rule.enabled !== false;
                const badgeClass = enabled ? "active" : "inactive";
                const statusText = enabled ? "已启用" : "未启用";
                const matchText = formatRoutingConditions(rule);
                const inboundIds = rule.inbound_node_ids || rule.inbound_node_id;
                const outboundIds = rule.outbound_node_ids || rule.outbound_node_id;
                return `
                    <tr>
                        <td><span class="status-badge ${badgeClass}" style="display:inline-flex;"><span class="status-dot"></span>${statusText}</span></td>
                        <td class="min-w-[180px] whitespace-normal py-3 px-3.5">
                            <strong>${esc(rule.name || "-")}</strong>
                            <div style="font-size:12px; color:var(--muted); margin-top:3px;">${esc(rule.status_text || "未写入 Xray")}</div>
                        </td>
                        <td>${esc(namesByIds(inboundIds, nodeNameById))}</td>
                        <td>${esc(matchText)}</td>
                        <td>${esc(namesByIds(outboundIds, outboundNameById))}</td>
                        <td>
                            <div class="flex gap-2 justify-end flex-wrap">
                                <button type="button" class="min-h-[30px] py-1 px-3 text-[11.5px] w-auto rounded-xl bg-glass border border-border text-text shadow-none hover:bg-glass-strong hover:shadow-none inline-flex justify-center items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed" onclick="${esc(`toggleRoutingRule(${jsArg(rule.id)}, ${enabled ? "false" : "true"})`)}" style="width:auto;">${enabled ? "停用" : "启用"}</button>
                                <button type="button" class="min-h-[30px] py-1 px-3 text-[11.5px] w-auto rounded-xl bg-glass border border-border text-text shadow-none hover:bg-glass-strong hover:shadow-none inline-flex justify-center items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed" onclick="${esc(`editRoutingRule(${jsArg(rule.id)})`)}" style="width:auto;">编辑</button>
                                <button type="button" class="min-h-[30px] py-1 px-3 text-[11.5px] w-auto rounded-xl bg-gradient-to-br from-[#ff5370] to-danger text-white border-none shadow-[0_12px_24px_rgba(255,83,112,0.24)] transition-all duration-[280ms] ease-in-out inline-flex justify-center items-center gap-2 hover:translate-y-[-2px] hover:shadow-[0_14px_28px_rgba(255,83,112,0.32)] active:translate-y-0 disabled:opacity-60 disabled:cursor-not-allowed" onclick="${esc(`deleteRoutingRule(${jsArg(rule.id)})`)}" style="width:auto;">删除</button>
                            </div>
                        </td>
                    </tr>
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
                inboundContainer.innerHTML = subscriptionLinks.length
                    ? subscriptionLinks.map(link => {
                        const checked = selectedInbounds.has(link.id) || (selectedInbounds.size === 0 && subscriptionLinks.length === 1) ? "checked" : "";
                        return `
                            <label class="flex items-center gap-2 py-1.5 px-3 bg-glass/40 border border-border rounded-xl cursor-pointer hover:border-primary/60 transition-all select-none">
                                <input type="checkbox" name="routing_inbound" value="${esc(link.id)}" ${checked} class="rounded border-border text-primary focus:ring-primary w-4 h-4">
                                <span class="text-[13px] font-semibold text-text">${esc(link.name)} <span class="text-[11px] text-muted font-normal">(${esc(subscriptionProtocolNames[link.protocol] || link.protocol)})</span></span>
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

        async function saveRoutingRule(event) {
            event.preventDefault();
            const err = $("routing_rule_error");
            const ok = $("routing_rule_success");
            const btn = $("routing_rule_submit");
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
                enabled: true
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
            btn.textContent = existing ? "保存中..." : "创建中...";
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
                setTimeout(closeRoutingRuleModal, 450);
            } catch (e) {
                err.textContent = "无法连接后端接口";
                err.style.display = "block";
            } finally {
                btn.disabled = false;
                btn.textContent = $("routing_rule_id").value ? "保存规则" : "创建规则";
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
