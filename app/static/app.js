function gatewayApp() {
    return {
        currentPage: 'keys',
        darkMode: false,
        currentUser: null,

        // Keys page
        keysData: {},
        expandedProvider: null,

        // Usage page
        usageData: {},
        usagePeriod: 'today',
        usageChart: null,
        usageEventsSource: null,
        usageRefreshTimer: null,

        // Logs page
        logsData: { total: 0, items: [] },
        logsOffset: 0,
        expandedLog: null,

        // Model application workflow
        prototypeModels: [
            { alias: 'volcengine-code', model: 'ark-code-latest', provider: 'volcengine', status: '可用', description: '适合编码任务，已完成连通性测试' },
            { alias: 'deepseek-chat', model: 'deepseek-v4-flash', provider: 'deepseek', status: '可用', description: '快速对话与通用文本处理' },
            { alias: 'reasoning', model: 'deepseek-reasoner', provider: 'deepseek', status: '可用', description: '复杂推理与问题分析' },
            { alias: 'openrouter-free', model: 'poolside/laguna-m.1-20260312:free', provider: 'openrouter', status: '可用', description: '免费额度模型通道' },
            { alias: 'qwen-plus', model: 'qwen-plus', provider: 'qwen', status: '可用', description: '中文内容与通用业务场景' },
        ],
        prototypeWorkflow: { requests: [], bindings: [] },
        prototypeFeedback: '',
        modelAdminTab: 'requests',

        // Users page
        usersData: { items: [] },
        userRoleFilter: 'all',
        userSearch: '',
        showUserModal: false,
        editingUserId: null,
        userForm: { username: '', password: '', email: '', phone: '', role: 'user', tenant: 'default' },
        userUseRoleDefault: true,
        userModuleSelection: [],

        // Config page
        configData: {},
        configBaseline: null,
        configSaved: false,
        modelHealth: {
            'cme-minimax': { ok: true, label: '可用', detail: 'minimax-m25，total_tokens 49' },
            'deepseek-chat': { ok: true, label: '可用', detail: 'deepseek-v4-flash，total_tokens 14' },
            'deepseek-r1': { ok: true, label: '可用', detail: 'deepseek-reasoner 已验证，total_tokens 16' },
            'reasoning': { ok: true, label: '可用', detail: 'deepseek-reasoner 已验证，total_tokens 16' },
            'openrouter-free': { ok: true, label: '可用', detail: 'poolside/laguna-m.1-20260312:free，total_tokens 61' },
            'psydo-gpt': { ok: true, label: '可用', detail: 'gpt-5.4，total_tokens 454' },
            'fast': { ok: false, label: '不可用', detail: 'OpenAI provider 未配置 key' },
            'smart': { ok: false, label: '不可用', detail: 'Anthropic provider 未配置 key' },
            'gemini-flash': { ok: false, label: '不可用', detail: 'Gemini 上游 403 Forbidden，检查 key 权限/API/模型' },
            'gemini-3.5-flash': { ok: false, label: '不可用', detail: 'Gemini 上游 403 Forbidden，且模型名疑似不正确' },
            'volcengine-code': { ok: true, label: '可用', detail: 'ark-code-latest 已验证；底层模型含 deepseek-v3-2、doubao-seed-code、doubao-seed-2.0-code、doubao-seed-2.0-pro、doubao-seed-2.0-lite、glm-4.7、kimi-k2.5、minimax-m2.5' },
            'ark-code-latest': { ok: true, label: '可用', detail: '火山 ark-code-latest 已验证；底层模型含 deepseek-v3-2、doubao-seed-code、doubao-seed-2.0-code、doubao-seed-2.0-pro、doubao-seed-2.0-lite、glm-4.7、kimi-k2.5、minimax-m2.5' },
            'qwen-plus': { ok: true, label: '可用', detail: 'qwen-plus，total_tokens 18' },
        },
        editingKeyPool: null,
        editKeyPoolBaseUrl: '',
        editKeyPoolKeys: [],
        editKeyPoolSelectedModel: '',
        plainKeysVisible: false,
        showModelWizard: false,
        modelWizard: { alias: '', model: '', provider: '' },
        showProviderWizard: false,
        providerWizard: { name: '', baseUrl: '', strategy: 'round-robin', rateLimit: 60, key: '' },

        init() {
            const saved = localStorage.getItem('gw_dark_mode');
            this.darkMode = saved === 'true';
            this.applyTheme();
            this.loadMe();
            this.$watch('currentPage', (page) => {
                if (page === 'usage') this.connectUsageEvents();
                else this.closeUsageEvents();
                if (page === 'models') {
                    this.loadAvailableModels();
                    this.loadModelRequestState();
                }
            });
            // Auto-refresh: keys every 30s, logs every 10s
            setInterval(() => { if (this.currentPage === 'keys') this.loadKeys(); }, 30000);
            setInterval(() => { if (this.currentPage === 'logs') { this.logsOffset = 0; this.loadLogs(); } }, 10000);
        },

        applyTheme() {
            document.documentElement.classList.toggle('dark', this.darkMode);
        },

        toggleTheme() {
            this.darkMode = !this.darkMode;
            localStorage.setItem('gw_dark_mode', this.darkMode);
            this.applyTheme();
        },

        async api(path, options = {}) {
            const headers = options.headers || {};
            headers['Content-Type'] = headers['Content-Type'] || 'application/json';
            const resp = await fetch(path, { ...options, headers });
            if (resp.status === 401) {
                window.location.href = '/admin/login';
                throw new Error('Unauthorized');
            }
            if (!resp.ok) {
                const error = await resp.json().catch(() => ({}));
                window.alert(error.detail || '请求失败，请稍后重试。');
                throw new Error(error.detail || `HTTP ${resp.status}`);
            }
            return resp.json();
        },

        async loadMe() {
            try {
                const data = await this.api('/admin/api/auth/me');
                this.currentUser = data.user;
                if (!this.canAccessModule(this.currentPage)) {
                    this.currentPage = this.currentUser.role === 'user'
                        ? 'models'
                        : (this.currentUser.modules || this.roleModules(this.currentUser.role))[0] || 'usage';
                }
                await this.loadCurrentPageData();
            } catch (e) { console.error(e); }
        },

        async loadCurrentPageData() {
            if (!this.currentUser) return;
            if (this.currentPage === 'keys') await this.loadKeys();
            else if (this.currentPage === 'usage') await this.loadUsage();
            else if (this.currentPage === 'logs') await this.loadLogs();
            else if (this.currentPage === 'users') await this.loadUsers();
            else if (this.currentPage === 'config') await this.loadConfig();
            else if (this.currentPage === 'models') {
                await this.loadAvailableModels();
                await this.loadModelRequestState();
            }
        },

        roleModules(role) {
            return {
                admin: ['keys', 'usage', 'logs', 'users', 'config'],
                operator: ['keys', 'usage', 'logs'],
                user: ['usage', 'logs'],
            }[role || 'user'] || ['usage', 'logs'];
        },

        allModules() {
            return ['keys', 'usage', 'logs', 'users', 'config'];
        },

        moduleLabel(module) {
            return {
                keys: '密钥池',
                usage: '用量统计',
                logs: '请求日志',
                users: '用户与权限',
                config: '配置管理',
                models: '模型申请',
            }[module] || module;
        },

        canAccessModule(module) {
            if (!this.currentUser) return true;
            if (module === 'models') return ['admin', 'user'].includes(this.currentUser.role);
            return (this.currentUser.modules || this.roleModules(this.currentUser.role)).includes(module);
        },

        prototypeUsername() {
            return this.currentUser?.username || 'demo-user@example.com';
        },

        prototypeBinding(username = this.prototypeUsername()) {
            const bindings = this.prototypeWorkflow.bindings || [];
            if (Array.isArray(bindings)) {
                return bindings.find((binding) => binding.username === username || binding.user_id === this.currentUser?.id) || null;
            }
            return bindings?.[username] || null;
        },

        prototypeRequestsForUser(username = this.prototypeUsername()) {
            return (this.prototypeWorkflow.requests || [])
                .filter((request) => request.username === username)
                .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
        },

        latestPrototypeRequest(username = this.prototypeUsername()) {
            return this.prototypeRequestsForUser(username)[0] || null;
        },

        pendingPrototypeRequest(username = this.prototypeUsername()) {
            return this.prototypeRequestsForUser(username).find((request) => request.status === 'pending') || null;
        },

        prototypeModelByAlias(alias) {
            return this.prototypeModels.find((model) => model.alias === alias) || null;
        },

        canSubmitPrototypeRequest(model) {
            if (!model || model.status !== '可用' || this.pendingPrototypeRequest()) return false;
            const binding = this.prototypeBinding();
            return !(binding && binding.alias === model.alias);
        },

        async submitPrototypeRequest(model) {
            if (!this.canSubmitPrototypeRequest(model)) return;
            try {
                await this.api('/admin/api/model-requests', {
                    method: 'POST',
                    body: JSON.stringify({ alias: model.alias }),
                });
                this.prototypeFeedback = '申请已提交，请等待管理员审批。';
                await this.loadModelRequestState();
            } catch (e) { console.error(e); }
        },

        async approvePrototypeRequest(requestId) {
            try {
                await this.api(`/admin/api/model-requests/${requestId}/approve`, { method: 'POST' });
                this.prototypeFeedback = '模型申请已通过。';
                await this.loadModelRequestState();
            } catch (e) { console.error(e); }
        },

        async rejectPrototypeRequest(requestId) {
            try {
                await this.api(`/admin/api/model-requests/${requestId}/reject`, { method: 'POST' });
                this.prototypeFeedback = '模型申请已拒绝。';
                await this.loadModelRequestState();
            } catch (e) { console.error(e); }
        },

        async stopModelBinding(bindingId) {
            if (!window.confirm('确认停止该用户使用此模型吗？停止后用户侧将立即不可用。')) return;
            try {
                await this.api(`/admin/api/model-bindings/${bindingId}/stop`, { method: 'POST' });
                this.prototypeFeedback = '已停止该用户使用模型。';
                await this.loadModelRequestState();
            } catch (e) { console.error(e); }
        },

        async resumeModelBinding(bindingId) {
            try {
                await this.api(`/admin/api/model-bindings/${bindingId}/resume`, { method: 'POST' });
                this.prototypeFeedback = '已恢复该用户使用模型。';
                await this.loadModelRequestState();
            } catch (e) { console.error(e); }
        },

        prototypeRequestStatusLabel(status) {
            return { pending: '待审批', approved: '已通过', rejected: '已拒绝' }[status] || status;
        },

        prototypeRequestStatusClass(status) {
            return status === 'approved' ? 'status-ok' : status === 'pending' ? 'status-warn' : 'status-bad';
        },

        prototypeRequestTypeLabel(type) {
            return type === 'change' ? '申请更换' : '首次申请';
        },

        bindingStatusLabel(status) {
            return status === 'active' ? '使用中' : status === 'stopped' ? '已停止' : status;
        },

        bindingStatusClass(status) {
            return status === 'active' ? 'status-ok' : 'status-bad';
        },

        formatPrototypeTime(value) {
            return value ? value.replace('T', ' ').substring(0, 16) : '-';
        },

        async loadModelRequestState() {
            if (!this.currentUser || !['admin', 'user'].includes(this.currentUser.role)) return;
            try {
                const path = this.currentUser.role === 'admin'
                    ? '/admin/api/model-requests'
                    : '/admin/api/model-requests/me';
                const data = await this.api(path);
                this.prototypeWorkflow = {
                    requests: data.requests || [],
                    bindings: data.bindings || (data.binding ? [data.binding] : []),
                };
            } catch (e) { console.error(e); }
        },

        async loadAvailableModels() {
            try {
                const data = await this.api('/admin/api/models/available');
                if (Array.isArray(data.items) && data.items.length) {
                    this.prototypeModels = data.items.map((item) => ({
                        alias: item.alias,
                        model: item.model,
                        provider: item.provider || '-',
                        status: item.available ? '可用' : '不可用',
                        description: item.description || '来自当前网关配置的模型别名',
                    }));
                }
            } catch (e) {
                console.warn('可申请模型列表加载失败，继续使用原型演示数据', e);
            }
        },

        async logout() {
            this.closeUsageEvents();
            await fetch('/admin/api/auth/logout', { method: 'POST' });
            window.location.href = '/admin/login';
        },

        async toggleProvider(name) {
            this.expandedProvider = this.expandedProvider === name ? null : name;
            if (this.expandedProvider && !Object.keys(this.configData || {}).length) {
                await this.loadConfig();
            }
        },

        async loadKeys() {
            if (this.currentUser && !this.canAccessModule('keys')) {
                return;
            }
            try {
                this.keysData = await this.api('/v1/keys/status');
            } catch (e) { console.error(e); }
        },

        async loadUsage() {
            try {
                this.usageData = await this.api('/v1/usage/summary?period=' + this.usagePeriod);
                this.$nextTick(() => this.renderChart());
                this.connectUsageEvents();
            } catch (e) { console.error(e); }
        },

        connectUsageEvents() {
            if (this.usageEventsSource || this.currentPage !== 'usage' || !window.EventSource) return;
            const source = new EventSource('/v1/usage/events');
            source.addEventListener('usage_recorded', () => {
                if (this.currentPage === 'usage') this.scheduleUsageRefresh();
            });
            source.onerror = () => {
                source.close();
                if (this.usageEventsSource === source) this.usageEventsSource = null;
            };
            this.usageEventsSource = source;
        },

        closeUsageEvents() {
            if (this.usageRefreshTimer) {
                clearTimeout(this.usageRefreshTimer);
                this.usageRefreshTimer = null;
            }
            if (this.usageEventsSource) {
                this.usageEventsSource.close();
                this.usageEventsSource = null;
            }
        },

        scheduleUsageRefresh() {
            if (this.usageRefreshTimer) clearTimeout(this.usageRefreshTimer);
            this.usageRefreshTimer = setTimeout(() => {
                this.usageRefreshTimer = null;
                this.loadUsage();
            }, 300);
        },

        renderChart() {
            const canvas = document.getElementById('usageChart');
            if (!canvas) return;
            if (this.usageChart) this.usageChart.destroy();

            const byModel = this.usageData.by_model || {};
            const labels = Object.keys(byModel);
            const tokens = labels.map(l => byModel[l].tokens || 0);
            const costs = labels.map(l => byModel[l].cost || 0);

            const isDark = this.darkMode;
            const textColor = isDark ? '#9ca3af' : '#6b7280';
            const gridColor = isDark ? '#374151' : '#e5e7eb';

            this.usageChart = new Chart(canvas, {
                type: 'bar',
                data: {
                    labels,
                    datasets: [
                        { label: 'Tokens', data: tokens, backgroundColor: '#3b82f6' },
                        { label: 'Cost ($)', data: costs, backgroundColor: '#10b981' },
                    ]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { labels: { color: textColor } } },
                    scales: {
                        x: { ticks: { color: textColor }, grid: { color: gridColor } },
                        y: { ticks: { color: textColor }, grid: { color: gridColor } }
                    }
                }
            });
        },

        async loadLogs() {
            try {
                const data = await this.api('/v1/logs?limit=50&offset=' + this.logsOffset);
                if (this.logsOffset === 0) {
                    this.logsData = data;
                } else {
                    this.logsData.items = [...this.logsData.items, ...data.items];
                    this.logsData.total = data.total;
                }
            } catch (e) { console.error(e); }
        },

        formatLogTime(value) {
            if (!value) return '-';
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) return String(value).substring(11, 19) || '-';
            return date.toLocaleString('zh-CN', {
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false,
            });
        },

        async loadUsers() {
            if (this.currentUser && !this.canAccessModule('users')) {
                this.currentPage = (this.currentUser.modules || this.roleModules(this.currentUser.role))[0] || 'usage';
                return;
            }
            try {
                this.usersData = await this.api('/admin/api/users');
            } catch (e) { console.error(e); }
        },

        filteredUsers() {
            const keyword = this.userSearch.trim().toLowerCase();
            return (this.usersData.items || []).filter((user) => {
                const roleMatched = this.userRoleFilter === 'all' || user.role === this.userRoleFilter;
                const text = [user.username, user.email, user.phone, user.tenant, user.role]
                    .filter(Boolean)
                    .join(' ')
                    .toLowerCase();
                return roleMatched && (!keyword || text.includes(keyword));
            });
        },

        openCreateUser() {
            this.editingUserId = null;
            this.userForm = { username: '', password: '', email: '', phone: '', role: 'user', tenant: 'default' };
            this.userUseRoleDefault = true;
            this.userModuleSelection = this.roleModules('user');
            this.showUserModal = true;
        },

        openEditUser(user) {
            this.editingUserId = user.id;
            this.userForm = {
                username: user.username || '',
                password: '',
                email: user.email || '',
                phone: user.phone || '',
                role: user.role || 'user',
                tenant: user.tenant || 'default',
            };
            this.userUseRoleDefault = user.module_overrides === null || user.module_overrides === undefined;
            this.userModuleSelection = [...(user.module_overrides || user.modules || this.roleModules(user.role))];
            this.showUserModal = true;
        },

        toggleUserModule(module) {
            if (this.userModuleSelection.includes(module)) {
                this.userModuleSelection = this.userModuleSelection.filter((item) => item !== module);
            } else {
                this.userModuleSelection = [...this.userModuleSelection, module];
            }
        },

        effectiveUserModules(user) {
            return user.modules || this.roleModules(user.role);
        },

        async saveUser() {
            const method = this.editingUserId ? 'PUT' : 'POST';
            const path = this.editingUserId ? `/admin/api/users/${this.editingUserId}` : '/admin/api/users';
            const payload = {
                ...this.userForm,
                module_overrides: this.userUseRoleDefault ? null : this.userModuleSelection,
            };
            try {
                await this.api(path, {
                    method,
                    body: JSON.stringify(payload),
                });
                this.showUserModal = false;
                await this.loadUsers();
                await this.loadMe();
            } catch (e) { console.error(e); }
        },

        async deleteUser(user) {
            if (!window.confirm(`确认删除用户「${user.username}」吗？该用户的登录会话也会被清理。`)) return;
            try {
                await this.api(`/admin/api/users/${user.id}`, { method: 'DELETE' });
                await this.loadUsers();
            } catch (e) { console.error(e); }
        },

        async loadConfig() {
            if (this.currentUser && !this.canAccessModule('config')) {
                this.currentPage = (this.currentUser.modules || this.roleModules(this.currentUser.role))[0] || 'usage';
                return;
            }
            try {
                this.configData = await this.api('/v1/config');
                this.configBaseline = this.cloneConfig(this.configData);
            } catch (e) { console.error(e); }
        },

        async saveConfig() {
            try {
                const summary = this.configChangeSummary();
                if (!window.confirm(summary)) return;
                await this.api('/v1/config', {
                    method: 'PUT',
                    body: JSON.stringify(this.configData),
                });
                this.configSaved = true;
                setTimeout(() => { this.configSaved = false; }, 3000);
                await this.loadConfig();
                await this.loadKeys();
            } catch (e) { console.error(e); }
        },

        cloneConfig(data) {
            return JSON.parse(JSON.stringify(data || {}));
        },

        stableJson(value) {
            return JSON.stringify(value ?? null);
        },

        diffMap(before = {}, after = {}, label) {
            const changes = [];
            const keys = Array.from(new Set([...Object.keys(before || {}), ...Object.keys(after || {})])).sort();
            for (const key of keys) {
                const oldValue = before?.[key];
                const newValue = after?.[key];
                if (oldValue === undefined) changes.push(`新增${label}：${key}`);
                else if (newValue === undefined) changes.push(`删除${label}：${key}`);
                else if (this.stableJson(oldValue) !== this.stableJson(newValue)) changes.push(`修改${label}：${key}`);
            }
            return changes;
        },

        routeLabel(route) {
            if (!route) return '-';
            return `${route.pattern || '-'} -> ${route.provider || '-'}`;
        },

        diffRoutes(before = [], after = []) {
            const beforeSet = new Set((before || []).map((route) => this.routeLabel(route)));
            const afterSet = new Set((after || []).map((route) => this.routeLabel(route)));
            const changes = [];
            for (const item of afterSet) if (!beforeSet.has(item)) changes.push(`新增路由：${item}`);
            for (const item of beforeSet) if (!afterSet.has(item)) changes.push(`删除路由：${item}`);
            if (!changes.length && this.stableJson(before) !== this.stableJson(after)) {
                changes.push('调整路由顺序或内容');
            }
            return changes;
        },

        diffKeypools(before = {}, after = {}) {
            const changes = [];
            const names = Array.from(new Set([...Object.keys(before || {}), ...Object.keys(after || {})])).sort();
            for (const name of names) {
                const oldPool = before?.[name];
                const newPool = after?.[name];
                if (!oldPool) {
                    changes.push(`新增密钥池：${name}`);
                    continue;
                }
                if (!newPool) {
                    changes.push(`删除密钥池：${name}`);
                    continue;
                }
                if ((oldPool.strategy || '') !== (newPool.strategy || '')) {
                    changes.push(`修改密钥池策略：${name}（${oldPool.strategy || '-'} -> ${newPool.strategy || '-'}）`);
                }
                if ((oldPool.rate_limit || '') !== (newPool.rate_limit || '')) {
                    changes.push(`修改密钥池限速：${name}`);
                }
                const oldKeys = oldPool.keys || [];
                const newKeys = newPool.keys || [];
                if (oldKeys.length !== newKeys.length) {
                    changes.push(`修改密钥数量：${name}（${oldKeys.length} -> ${newKeys.length}）`);
                } else if (this.stableJson(oldKeys) !== this.stableJson(newKeys)) {
                    changes.push(`修改密钥内容：${name}`);
                }
            }
            return changes;
        },

        configChangeSummary() {
            if (!this.configBaseline) {
                return '未找到原始配置快照，确认保存当前配置吗？';
            }
            const before = this.configBaseline;
            const after = this.configData || {};
            const changes = [
                ...this.diffRoutes(before.routes || [], after.routes || []),
                ...this.diffMap(before.aliases || {}, after.aliases || {}, '模型别名'),
                ...this.diffMap(before.provider_base_urls || {}, after.provider_base_urls || {}, 'Base URL'),
                ...this.diffKeypools(before.keypools || {}, after.keypools || {}),
                ...this.diffMap(before.fallbacks || {}, after.fallbacks || {}, '降级配置'),
            ];

            if (!changes.length) {
                return '未检测到配置变化，仍然保存吗？';
            }

            const shown = changes.slice(0, 12);
            const more = changes.length > shown.length ? `\n还有 ${changes.length - shown.length} 项变化未显示。` : '';
            return `保存前请确认以下变化：\n\n${shown.map((item) => `- ${item}`).join('\n')}${more}\n\n确认保存并生效吗？`;
        },

        confirmDelete(message) {
            return window.confirm(message || '确认删除吗？此操作需要保存配置后才会生效。');
        },

        deleteRoute(index) {
            const route = this.configData.routes?.[index];
            const label = route ? `${route.pattern || '-'} -> ${route.provider || '-'}` : '这条路由规则';
            if (!this.confirmDelete(`确认删除路由规则「${label}」吗？`)) return;
            this.configData.routes.splice(index, 1);
        },

        deleteAlias(alias) {
            const model = this.configData.aliases?.[alias] || '-';
            if (!this.confirmDelete(`确认删除模型别名「${alias} -> ${model}」吗？`)) return;
            delete this.configData.aliases[alias];
        },

        modelHealthInfo(alias, model) {
            return this.modelHealth[alias]
                || this.modelHealth[model]
                || { ok: null, label: '未测试', detail: '尚未执行连通性测试' };
        },

        addAlias() {
            this.configData.aliases = this.configData.aliases || {};
            let index = 1;
            let alias = 'new_alias';
            while (Object.prototype.hasOwnProperty.call(this.configData.aliases, alias)) {
                alias = `new_alias_${index}`;
                index += 1;
            }
            this.configData.aliases[alias] = '';
        },

        renameAlias(oldAlias, newAliasRaw) {
            const newAlias = String(newAliasRaw || '').trim();
            if (!newAlias || newAlias === oldAlias) return;
            this.configData.aliases = this.configData.aliases || {};
            if (Object.prototype.hasOwnProperty.call(this.configData.aliases, newAlias)) {
                window.alert(`模型别名「${newAlias}」已存在，请换一个名称。`);
                return;
            }
            const next = {};
            for (const [alias, model] of Object.entries(this.configData.aliases)) {
                next[alias === oldAlias ? newAlias : alias] = model;
            }
            this.configData.aliases = next;
        },

        openModelWizard() {
            const providers = this.providerNames();
            this.modelWizard = {
                alias: '',
                model: '',
                provider: providers[0] || '',
            };
            this.showModelWizard = true;
        },

        providerNames() {
            return Array.from(new Set([
                ...Object.keys(this.configData.keypools || {}),
                ...(this.configData.routes || []).map((route) => route.provider).filter(Boolean),
            ])).sort();
        },

        openProviderWizard() {
            this.providerWizard = {
                name: '',
                baseUrl: '',
                strategy: 'round-robin',
                rateLimit: 60,
                key: '',
            };
            this.showProviderWizard = true;
        },

        saveProviderWizard() {
            const name = this.providerWizard.name.trim().toLowerCase();
            const baseUrl = this.providerWizard.baseUrl.trim();
            const key = this.providerWizard.key.trim();
            const rateLimit = Number(this.providerWizard.rateLimit) || 60;
            const strategy = this.providerWizard.strategy || 'round-robin';

            if (!/^[a-z0-9_-]+$/.test(name)) {
                window.alert('Provider 名称只能包含小写字母、数字、下划线和短横线。');
                return;
            }
            if (!baseUrl) {
                window.alert('请填写 Base URL。');
                return;
            }
            if (this.configData.keypools?.[name]) {
                window.alert(`Provider「${name}」已存在。`);
                return;
            }

            this.configData.provider_base_urls = this.configData.provider_base_urls || {};
            this.configData.keypools = this.configData.keypools || {};
            this.configData.pricing = this.configData.pricing || {};

            this.configData.provider_base_urls[name] = baseUrl;
            this.configData.keypools[name] = {
                keys: key ? [key] : [],
                strategy,
                rate_limit: rateLimit,
            };
            this.configData.pricing[name] = this.configData.pricing[name] || {};
            this.showProviderWizard = false;
        },

        ensureModelRoute(model, provider) {
            const routes = this.configData.routes = this.configData.routes || [];
            const alreadyMatched = routes.some((route) => {
                return route.provider === provider && this.routeMatchesModel(route.pattern, model);
            });
            if (alreadyMatched) return;

            const fallbackIndex = routes.findIndex((route) => route.pattern === '*');
            const route = { pattern: model, provider };
            if (fallbackIndex >= 0) routes.splice(fallbackIndex, 0, route);
            else routes.push(route);
        },

        saveModelWizard() {
            const alias = this.modelWizard.alias.trim();
            const model = this.modelWizard.model.trim();
            const provider = this.modelWizard.provider;
            if (!alias || !model || !provider) {
                window.alert('请填写模型别名、实际模型名并选择 Provider。');
                return;
            }

            this.configData.aliases = this.configData.aliases || {};
            if (Object.prototype.hasOwnProperty.call(this.configData.aliases, alias)) {
                window.alert(`模型别名「${alias}」已存在，请换一个名称。`);
                return;
            }

            this.configData.aliases[alias] = model;
            this.ensureModelRoute(model, provider);
            this.showModelWizard = false;
        },

        deleteFallback(index) {
            const fallback = this.configData.fallbacks?.[index];
            const label = fallback ? `${fallback.from || '-'} -> ${fallback.to || '-'}` : '这条降级配置';
            if (!this.confirmDelete(`确认删除降级配置「${label}」吗？`)) return;
            this.configData.fallbacks.splice(index, 1);
        },

        deleteEditingKey(index) {
            const label = this.editingKeyPool || '当前提供商';
            if (!this.confirmDelete(`确认删除「${label}」的第 ${index + 1} 个密钥吗？`)) return;
            this.editKeyPoolKeys.splice(index, 1);
        },

        editKeyPool(name) {
            this.editingKeyPool = name;
            this.editKeyPoolBaseUrl = this.providerBaseUrl(name);
            this.editKeyPoolKeys = [...(this.configData.keypools?.[name]?.keys || [])];
            this.editKeyPoolSelectedModel = this.providerModels(name)[0]?.value || '';
            this.plainKeysVisible = false;
        },

        providerBaseUrl(name) {
            const defaults = {
                openai: 'https://api.openai.com/v1',
                anthropic: 'https://api.anthropic.com',
                deepseek: 'https://api.deepseek.com',
                qwen: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                wenxin: 'https://qianfan.baidubce.com/v2',
                zhipu: 'https://open.bigmodel.cn/api/paas/v4',
                volcengine: 'https://ark.cn-beijing.volces.com/api/coding/v3',
                openrouter: 'https://openrouter.ai/api/v1',
                cmecloud: 'https://zhenze-huhehaote.cmecloud.cn/api/coding/v1',
                psydo: 'https://api.psydo.top/v1',
                gemini: 'https://generativelanguage.googleapis.com/v1beta/openai',
            };
            return this.configData.provider_base_urls?.[name] || defaults[name] || '';
        },

        routeMatchesModel(pattern, model) {
            if (!pattern || !model) return false;
            const escaped = pattern.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*');
            return new RegExp('^' + escaped + '$').test(model);
        },

        providerForModel(model) {
            const routes = this.configData.routes || [];
            for (const route of routes) {
                if (this.routeMatchesModel(route.pattern, model)) return route.provider;
            }
            return routes.length ? routes[routes.length - 1].provider : '';
        },

        providerModels(name) {
            const models = [];
            const seen = new Set();
            const add = (value, label, source) => {
                if (!value || seen.has(source + ':' + value)) return;
                seen.add(source + ':' + value);
                models.push({ value, label, source });
            };

            for (const [alias, model] of Object.entries(this.configData.aliases || {})) {
                if (this.providerForModel(model) === name) {
                    add(model, `${alias} -> ${model}`, 'alias');
                }
            }

            for (const model of Object.keys(this.configData.pricing?.[name] || {})) {
                add(model, model, 'pricing');
            }

            for (const route of this.configData.routes || []) {
                if (route.provider === name && route.pattern !== '*') {
                    add(route.pattern, route.pattern, 'route');
                }
            }

            return models;
        },

        async revealKeyPoolKeys() {
            if (!this.editingKeyPool) return;
            try {
                const data = await this.api('/v1/config/keypools/' + encodeURIComponent(this.editingKeyPool) + '/keys');
                this.editKeyPoolKeys = [...(data.keys || [])];
                this.plainKeysVisible = true;
            } catch (e) { console.error(e); }
        },

        saveKeyPool() {
            if (this.editingKeyPool && this.configData.keypools) {
                this.configData.provider_base_urls = this.configData.provider_base_urls || {};
                const baseUrl = this.editKeyPoolBaseUrl.trim();
                if (baseUrl) this.configData.provider_base_urls[this.editingKeyPool] = baseUrl;
                else delete this.configData.provider_base_urls[this.editingKeyPool];

                const original = this.configData.keypools[this.editingKeyPool]?.keys || [];
                const merged = this.editKeyPoolKeys.map((k, i) => {
                    if (k === '****' && i < original.length) return original[i];
                    return k;
                });
                this.configData.keypools[this.editingKeyPool].keys = merged;
            }
            this.editingKeyPool = null;
            this.editKeyPoolBaseUrl = '';
            this.editKeyPoolSelectedModel = '';
            this.plainKeysVisible = false;
        },
    };
}
