
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
            const node = subscriptionNodes.find(item => item.id === id);
            if (!node) return id || "-";
            const linkName = subscriptionLinkNameById(node.subscription_id);
            return linkName && linkName !== "-" ? `${linkName} / ${node.name}` : node.name;
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

        function handleRoutingConditionChange(event) {
            const allCheckbox = $("routing_match_all");
            const map = [
                ["routing_match_domain", "routing_match_domain_values"],
                ["routing_match_ip", "routing_match_ip_values"],
                ["routing_match_port", "routing_match_port_values"]
            ];
            const target = event && event.target ? event.target : null;
            if (target === allCheckbox && allCheckbox.checked) {
                map.forEach(([checkboxId, inputId]) => {
                    const checkbox = $(checkboxId);
                    const input = $(inputId);
                    if (!checkbox || !input) return;
                    checkbox.checked = false;
                    input.value = "";
                });
            } else {
                const hasSpecificMatch = map.some(([checkboxId]) => {
                    const checkbox = $(checkboxId);
                    return checkbox && checkbox.checked;
                });
                if (allCheckbox) allCheckbox.checked = !hasSpecificMatch;
            }
            map.forEach(([checkboxId, inputId]) => {
                const checkbox = $(checkboxId);
                const input = $(inputId);
                if (!checkbox || !input) return;
                input.disabled = !checkbox.checked;
                if (!checkbox.checked) input.value = "";
            });
        }

        function resetRoutingConditions() {
            $("routing_match_all").checked = true;
            $("routing_match_domain").checked = false;
            $("routing_match_ip").checked = false;
            $("routing_match_port").checked = false;
            $("routing_match_domain_values").value = "";
            $("routing_match_ip_values").value = "";
            $("routing_match_port_values").value = "";
            handleRoutingConditionChange();
        }

        function collectRoutingConditions() {
            const conditions = [];
            if ($("routing_match_all").checked) {
                conditions.push({ type: "all", value: "" });
            }
            const typed = [
                ["routing_match_domain", "routing_match_domain_values", "domain"],
                ["routing_match_ip", "routing_match_ip_values", "ip"],
                ["routing_match_port", "routing_match_port_values", "port"]
            ];
            typed.forEach(([checkboxId, inputId, type]) => {
                if (!$(checkboxId).checked) return;
                const values = splitInputValues($(inputId).value);
                values.forEach(value => conditions.push({ type, value }));
            });
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
                                <button type="button" class="min-h-[30px] py-1 px-3 text-[11.5px] w-auto rounded-xl bg-glass border border-border text-text shadow-none hover:bg-glass-strong hover:shadow-none inline-flex justify-center items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed" onclick="toggleRoutingRule('${esc(rule.id)}', ${enabled ? "false" : "true"})" style="width:auto;">${enabled ? "停用" : "启用"}</button>
                                <button type="button" class="min-h-[30px] py-1 px-3 text-[11.5px] w-auto rounded-xl bg-glass border border-border text-text shadow-none hover:bg-glass-strong hover:shadow-none inline-flex justify-center items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed" onclick="editRoutingRule('${esc(rule.id)}')" style="width:auto;">编辑</button>
                                <button type="button" class="min-h-[30px] py-1 px-3 text-[11.5px] w-auto rounded-xl bg-gradient-to-br from-[#ff5370] to-danger text-white border-none shadow-[0_12px_24px_rgba(255,83,112,0.24)] transition-all duration-[280ms] ease-in-out inline-flex justify-center items-center gap-2 hover:translate-y-[-2px] hover:shadow-[0_14px_28px_rgba(255,83,112,0.32)] active:translate-y-0 disabled:opacity-60 disabled:cursor-not-allowed" onclick="deleteRoutingRule('${esc(rule.id)}')" style="width:auto;">删除</button>
                            </div>
                        </td>
                    </tr>
                `;
            }).join("");
        }

        function setSelectedOptions(select, values) {
            if (!select) return;
            const selected = new Set(asArray(values).map(value => String(value)));
            Array.from(select.options).forEach(option => {
                option.selected = selected.has(option.value);
            });
        }

        function availableOutboundOptionsForRule(rule) {
            if (!rule) return outboundOptions();
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
            resetRoutingConditions();
            const conditions = rule ? routingConditions(rule) : [];
            if (!conditions.length) return;
            $("routing_match_all").checked = conditions.some(item => item.type === "all");
            const grouped = { domain: [], ip: [], port: [] };
            conditions.forEach(item => {
                if (grouped[item.type] && item.value) grouped[item.type].push(item.value);
            });
            [
                ["domain", "routing_match_domain", "routing_match_domain_values"],
                ["ip", "routing_match_ip", "routing_match_ip_values"],
                ["port", "routing_match_port", "routing_match_port_values"]
            ].forEach(([type, checkboxId, inputId]) => {
                const values = grouped[type];
                $(checkboxId).checked = values.length > 0;
                $(inputId).value = values.join("\n");
            });
            handleRoutingConditionChange();
        }

        async function openRoutingRuleModal(ruleId = "") {
            if (!subscriptionNodes.length) await loadSubscriptionWorkspace();
            await loadRoutingRules();
            const rule = routingRules.find(item => item.id === ruleId) || null;
            const inboundSelect = $("routing_rule_inbound");
            const outboundSelect = $("routing_rule_outbound");
            inboundSelect.innerHTML = subscriptionNodes.length
                ? subscriptionNodes.map(node => `<option value="${esc(node.id)}">${esc(nodeNameById(node.id))} / ${esc(subscriptionProtocolNames[node.protocol] || node.protocol)}</option>`).join("")
                : `<option value="">请先创建订阅节点</option>`;
            outboundSelect.innerHTML = availableOutboundOptionsForRule(rule).map(node => `<option value="${esc(node.id)}">${esc(node.name)}</option>`).join("");
            $("routing_rule_id").value = rule ? rule.id : "";
            $("routing_rule_name").value = rule ? (rule.name || "") : "";
            setSelectedOptions(inboundSelect, rule ? (rule.inbound_node_ids || rule.inbound_node_id) : []);
            setSelectedOptions(outboundSelect, rule ? (rule.outbound_node_ids || rule.outbound_node_id) : []);
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

        function handleRoutingRuleMatchChange(value) {
            handleRoutingConditionChange();
        }

        async function saveRoutingRule(event) {
            event.preventDefault();
            const err = $("routing_rule_error");
            const ok = $("routing_rule_success");
            const btn = $("routing_rule_submit");
            err.style.display = "none";
            ok.style.display = "none";
            const payload = {
                id: $("routing_rule_id").value,
                name: $("routing_rule_name").value.trim(),
                inbound_node_ids: selectedValues("routing_rule_inbound"),
                outbound_node_ids: selectedValues("routing_rule_outbound"),
                match_conditions: collectRoutingConditions(),
                enabled: true
            };
            const existing = routingRules.find(item => item.id === payload.id);
            if (existing) payload.enabled = existing.enabled !== false;
            if (!payload.match_conditions.length) {
                err.textContent = "请至少选择一个匹配方式";
                err.style.display = "block";
                return;
            }
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
