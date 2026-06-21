
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
                input.select();
                document.execCommand("copy");
                showToast("复制成功", "success");
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
        function time(ts) { return ts ? new Date(ts * 1000).toLocaleString() : "从未" }
        function speed(v) { return v ? `${(v * 8 / 1000 / 1000).toFixed(1)} Mbps` : "-" }

        function formatBytes(bytes, decimals = 2) {
            if (!bytes || bytes === 0) return '0 Bytes';
            const k = 1024;
            const dm = decimals < 0 ? 0 : decimals;
            const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
        }

        function applyTheme(themeName) {
            document.body.className = themeName === 'default' ? '' : themeName;
            localStorage.setItem('theme', themeName);
            document.querySelectorAll('.theme-option').forEach(card => card.classList.remove('active'));
            const selectedCard = document.querySelector(`.theme-option[data-theme="${themeName}"]`);
            if (selectedCard) selectedCard.classList.add('active');
        }

        function showTab(tabId) {
            const tabs = document.querySelectorAll('main > section[id^="tab-"]');
            tabs.forEach(tab => {
                tab.classList.remove('active', 'block');
                tab.classList.add('hidden');
            });

            const targetTab = document.getElementById(tabId);
            if (targetTab) {
                targetTab.classList.remove('hidden');
                targetTab.classList.add('active', 'block');
            }

            const menuItems = document.querySelectorAll('.menu-item');
            menuItems.forEach(item => item.classList.remove('active', 'active-menu-item'));

            const menuId = tabId.replace('tab-', 'menu-');
            const activeItem = document.getElementById(menuId);
            if (activeItem) {
                activeItem.classList.add('active', 'active-menu-item');
            }

            sessionStorage.setItem('currentTab', tabId);

            // Tab-specific loading
            if (tabId === 'tab-host') {
                loadGatewayStatus();
                loadXrayPanel();
            } else if (tabId === 'tab-xray') {
                loadSubscriptionWorkspace();
            } else if (tabId === 'tab-nodes') {
                load();
                loadGatewayStatus();
                const selectedOutboundTab = document.querySelector('input[name="outbound_node_tab"]:checked');
                if (typeof showOutboundNodeTab === "function") {
                    showOutboundNodeTab(selectedOutboundTab ? selectedOutboundTab.value : "vpngate");
                }
            } else if (tabId === 'tab-settings') {
                loadGatewayStatus();
                loadLogs();
            } else if (tabId === 'tab-gateway') {
                loadRoutingRules();
            }
        }

        const rowIcon = (name) => {
            const icons = {
                add: `<path d="M12 5v14M5 12h14"></path>`,
                star: `<path d="m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.8 1-6.1-4.4-4.3 6.1-.9Z"></path>`,
                copy: `<rect x="9" y="9" width="11" height="11" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>`,
                qr: `<rect x="3" y="3" width="6" height="6" rx="1"></rect><rect x="15" y="3" width="6" height="6" rx="1"></rect><rect x="3" y="15" width="6" height="6" rx="1"></rect><path d="M15 15h2v2h-2z"></path><path d="M19 15h2"></path><path d="M15 19h6"></path><path d="M11 3h1"></path><path d="M11 7h1"></path><path d="M3 11h1"></path><path d="M7 11h1"></path>`,
                power: `<path d="M12 2v10M18.4 6.6a9 9 0 1 1-12.8 0"></path>`,
                edit: `<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"></path>`,
                trash: `<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v5M14 11v5"></path>`,
                check: `<polyline points="20 6 9 17 4 12"></polyline>`,
                play: `<path d="M5 3l14 9-14 9V3z"></path>`,
                stop: `<rect x="6" y="6" width="12" height="12"></rect>`
            };
            return `<svg class="row-action__icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">${icons[name] || icons.edit}</svg>`;
        };

        const actionButton = (label, icon, onclick, danger = false, showText = false, extraClass = '') => `
            <button type="button" class="row-action-btn ${extraClass}${danger ? " is-danger" : ""}${showText ? " row-action-btn--text" : ""}" onclick="${esc(onclick)}" title="${esc(label)}" aria-label="${esc(label)}">
                ${rowIcon(icon)}${showText ? `<span class="row-action__label">${esc(label)}</span>` : ""}
            </button>
        `;

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

        function featureDisabledHtml(title, message, key) {
            const messageHtml = message ? `<div>${esc(message)}</div>` : "";
            return `
                <div class="feature-disabled-panel">
                    <strong>${esc(title)}</strong>
                    ${messageHtml}
                </div>
            `;
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

        window.showTab = showTab;
        window.toggleFeaturePower = toggleFeaturePower;
        window.setFeatureGate = setFeatureGate;
        window.load = load;
        window.logoutAdmin = logoutAdmin;
        window.showToast = showToast;
        window.copyShareText = copyShareText;
        window.applyTheme = applyTheme;
        window.isFeatureEnabled = isFeatureEnabled;
        window.syncFeatureGates = syncFeatureGates;
        window.renderFeatureGateSwitches = renderFeatureGateSwitches;
        window.featureDisabledHtml = featureDisabledHtml;

        window.addEventListener('DOMContentLoaded', initApp);
