function gatewayApp() {
    return {
        currentPage: 'keys',
        darkMode: false,
        currentUser: null,

        // Keys page
        keysData: {},
        expandedProvider: null,
        selectedKeyProvider: '',

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
        logFilters: {
            status: '',
            provider: '',
            model: '',
            username: '',
            start: '',
            end: '',
        },

        // Audit page
        auditData: { total: 0, items: [] },
        auditOffset: 0,
        expandedAuditLog: null,

        // Model test prototype
        modelTestMode: 'system',
        modelTestForm: {
            provider: '',
            model: '',
            keySuffix: 'auto',
            apiKeyId: '',
            prompt: '你好，请回复 ok',
        },
        modelTestResult: null,
        modelTestLoading: false,
        modelTestProgressText: '',
        modelTestHistory: [],

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
        userApiKeys: { items: [] },
        newUserApiKey: '',
        revealedUserApiKeys: {},
        callConfigExampleTab: 'curl',
        callConfigCopyFeedback: '',

        // Users page
        usersData: { items: [] },
        userRoleFilter: 'all',
        userSearch: '',
        showUserModal: false,
        editingUserId: null,
        userForm: { username: '', password: '', email: '', phone: '', role: 'user', tenant: 'default' },
        userUseRoleDefault: true,
        userModuleSelection: [],

        // Registration approval
        registrationRequests: [],
        registrationFeedback: '',

        // Config page
        configData: {},
        configBaseline: null,
        configSaved: false,
        configTab: 'overview',
        configProviderSelection: '',
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
            'qwen3.6-plus': { ok: true, label: '可用', detail: 'qwen/qwen3.6-plus 已通过 moma.cmecloud.cn 连通性测试' },
            'qwen/qwen3.6-plus': { ok: true, label: '可用', detail: 'moma.cmecloud.cn/v1，真实模型 qwen/qwen3.6-plus 已测试通过' },
        },
        editingKeyPool: null,
        editKeyPoolBaseUrl: '',
        editKeyPoolKeys: [],
        editKeyPoolSelectedModel: '',
        plainKeysVisible: false,
        showModelWizard: false,
        modelWizard: { alias: '', model: '', provider: '' },
        showProviderWizard: false,
        providerWizard: { selectedModel: '', name: '', baseUrl: '', routePattern: '', strategy: 'round-robin', rateLimit: 60, key: '' },

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
                if (page === 'modelTest') this.loadModelTestPrototype();
                if (page === 'registration') this.loadRegistrationPrototype();
            });
            // Auto-refresh visible pages only. Quiet mode avoids stale console noise
            // when the local service is restarting.
            setInterval(() => {
                if (this.currentPage === 'keys' && this.canAccessModule('keys')) {
                    this.loadKeys({ quiet: true });
                }
            }, 30000);
            setInterval(() => {
                if (this.currentPage === 'logs' && this.canAccessModule('logs')) {
                    this.logsOffset = 0;
                    this.loadLogs({ quiet: true });
                }
            }, 10000);
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
            const { quiet = false, ...fetchOptions } = options;
            let resp;
            try {
                resp = await fetch(path, { ...fetchOptions, headers });
            } catch (error) {
                if (!quiet) console.error(error);
                throw error;
            }
            if (resp.status === 401) {
                window.location.href = '/admin/login';
                throw new Error('Unauthorized');
            }
            if (!resp.ok) {
                const error = await resp.json().catch(() => ({}));
                if (error.detail && typeof error.detail === 'object') {
                    error.detail = this.formatApiError(error, resp.status);
                }
                if (!quiet) window.alert(error.detail || '请求失败，请稍后重试。');
                throw new Error(this.formatApiError(error, resp.status));
            }
            return resp.json();
        },

        formatApiError(error, status) {
            const detail = error?.detail;
            if (detail && typeof detail === 'object') {
                const title = detail.message || `HTTP ${status}`;
                const errors = Array.isArray(detail.errors) ? detail.errors : [];
                return errors.length ? `${title}\n\n${errors.map((item) => `- ${item}`).join('\n')}` : title;
            }
            return detail || error?.message || `HTTP ${status}`;
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
            else if (this.currentPage === 'audit') await this.loadAuditLogs();
            else if (this.currentPage === 'registration') await this.loadRegistrationPrototype();
            else if (this.currentPage === 'users') await this.loadUsers();
            else if (this.currentPage === 'config') await this.loadConfig();
            else if (this.currentPage === 'modelTest') await this.loadModelTestPrototype();
            else if (this.currentPage === 'models') {
                await this.loadAvailableModels();
                await this.loadModelRequestState();
                if (this.currentUser.role === 'user') await this.loadUserApiKeys();
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
                audit: '操作日志',
                users: '用户与权限',
                config: '配置管理',
                models: '模型申请',
                modelTest: '模型测试',
            }[module] || module;
        },

        canAccessModule(module) {
            if (!this.currentUser) return true;
            if (module === 'models') return ['admin', 'user'].includes(this.currentUser.role);
            if (module === 'modelTest') return ['admin', 'operator', 'user'].includes(this.currentUser.role);
            if (module === 'registration') return this.currentUser.role === 'admin';
            return (this.currentUser.modules || this.roleModules(this.currentUser.role)).includes(module);
        },

        async loadModelTestPrototype() {
            if (!this.currentUser) return;
            this.modelTestMode = this.currentUser.role === 'user' ? 'personal' : 'system';
            if (this.modelTestMode === 'system') {
                await this.loadKeys({ quiet: true });
                await this.loadAvailableModels();
                if (this.canAccessModule('config')) await this.loadConfig();
                this.ensureSystemModelTestDefaults();
            } else {
                await this.loadModelRequestState();
                await this.loadUserApiKeys();
                this.ensurePersonalModelTestDefaults();
            }
        },

        modelTestProviders() {
            const names = new Set([
                ...Object.keys(this.keysData || {}),
                ...this.prototypeModels.map((model) => model.provider).filter(Boolean),
            ]);
            return [...names].filter((name) => name && name !== '-').sort();
        },

        modelTestModelsForProvider(provider = this.modelTestForm.provider) {
            return this.prototypeModels.filter((model) => model.provider === provider);
        },

        modelTestKeysForProvider(provider = this.modelTestForm.provider) {
            return this.keysData?.[provider]?.keys || [];
        },

        keyValue(key) {
            return typeof key === 'object' && key !== null ? (key.value || '') : (key || '');
        },

        keyAllowedModels(key) {
            return typeof key === 'object' && key !== null ? [...(key.allowed_models || [])] : [];
        },

        normalizeEditableKey(key) {
            return {
                value: this.keyValue(key),
                allowed_models: this.keyAllowedModels(key),
            };
        },

        activeUserApiKeys() {
            return (this.userApiKeys.items || []).filter((key) => key.status === 'active');
        },

        ensureSystemModelTestDefaults() {
            const providers = this.modelTestProviders();
            if (!providers.includes(this.modelTestForm.provider)) {
                this.modelTestForm.provider = providers[0] || '';
            }
            const models = this.modelTestModelsForProvider();
            if (!models.some((model) => model.model === this.modelTestForm.model)) {
                this.modelTestForm.model = models[0]?.model || '';
            }
            const keys = this.modelTestKeysForProvider();
            if (this.modelTestForm.keySuffix !== 'auto' && !keys.some((key) => key.suffix === this.modelTestForm.keySuffix)) {
                this.modelTestForm.keySuffix = 'auto';
            }
        },

        ensurePersonalModelTestDefaults() {
            const binding = this.prototypeBinding();
            if (binding) this.modelTestForm.model = binding.alias || binding.model || '';
            const activeKeys = this.activeUserApiKeys();
            if (!activeKeys.some((key) => String(key.id) === String(this.modelTestForm.apiKeyId))) {
                this.modelTestForm.apiKeyId = activeKeys[0]?.id || '';
            }
        },

        selectedModelTestModel() {
            return this.prototypeModels.find((model) => model.model === this.modelTestForm.model || model.alias === this.modelTestForm.model) || null;
        },

        selectedUserApiKeyForTest() {
            return this.activeUserApiKeys().find((key) => String(key.id) === String(this.modelTestForm.apiKeyId)) || null;
        },

        addModelTestHistory(result) {
            this.modelTestHistory = [
                { ...result, id: Date.now(), mode: this.modelTestMode },
                ...this.modelTestHistory,
            ].slice(0, 5);
        },

        modelTestResultFromResponse(data, fallback = {}) {
            const ok = data.status === 'success';
            const result = {
                ok,
                title: ok ? '真实调用通过' : '真实调用失败',
                detail: ok
                    ? `返回内容：${data.content || '无文本内容'}`
                    : (data.error || '上游调用失败'),
                suggestion: ok ? '' : (data.suggestion || '请检查模型、Key、Base URL 或上游账号权限。'),
                model: data.model || fallback.model || '-',
                provider: data.provider || fallback.provider || '-',
                key: data.user_api_key_prefix ? `${data.user_api_key_prefix}...` : (data.key_suffix ? `****${data.key_suffix}` : fallback.key || '-'),
                base_url: data.base_url || fallback.base_url || '',
                latency_ms: data.latency_ms,
                total_tokens: data.usage?.total_tokens || 0,
                created_at: data.tested_at || new Date().toISOString(),
            };
            return result;
        },

        async runPrototypeModelTest() {
            const startedAt = new Date();
            if (this.modelTestMode === 'personal') {
                const binding = this.prototypeBinding();
                const apiKey = this.selectedUserApiKeyForTest();
                if (!binding) {
                    this.modelTestResult = {
                        ok: false,
                        title: '无法测试',
                        detail: '当前账号还没有审批通过的模型绑定。',
                        suggestion: '请先在申请模型页面提交申请，并等待管理员审批通过。',
                        created_at: startedAt.toISOString(),
                    };
                    this.addModelTestHistory(this.modelTestResult);
                    return;
                }
                if (binding.status === 'stopped') {
                    this.modelTestResult = {
                        ok: false,
                        title: '模型已停止',
                        detail: '管理员已停止你使用当前绑定模型。',
                        suggestion: '请联系管理员恢复使用，或重新申请其他模型。',
                        created_at: startedAt.toISOString(),
                    };
                    this.addModelTestHistory(this.modelTestResult);
                    return;
                }
                if (!apiKey) {
                    this.modelTestResult = {
                        ok: false,
                        title: '缺少可用 API Key',
                        detail: '当前账号没有 active 状态的用户级 API Key。',
                        suggestion: '请先在申请模型页面生成或重新生成 API Key。',
                        created_at: startedAt.toISOString(),
                    };
                    this.addModelTestHistory(this.modelTestResult);
                    return;
                }
                this.modelTestLoading = true;
                this.modelTestProgressText = '\u6b63\u5728\u68c0\u67e5\u4e2a\u4eba API Key \u548c\u5df2\u7ed1\u5b9a\u6a21\u578b...';
                this.modelTestResult = null;
                const loadingStartedAt = Date.now();
                try {
                    const data = await this.api('/admin/api/model-tests/run', {
                        method: 'POST',
                        quiet: true,
                        body: JSON.stringify({
                            mode: 'personal',
                            api_key_id: apiKey.id,
                            prompt: this.modelTestForm.prompt,
                        }),
                    });
                    this.modelTestResult = this.modelTestResultFromResponse(data, {
                        model: binding.model,
                        provider: binding.provider,
                        key: apiKey.key_prefix + '...',
                    });
                } catch (error) {
                    this.modelTestResult = {
                        ok: false,
                        title: '测试请求失败',
                        detail: error.message || '无法完成测试请求',
                        suggestion: '请检查登录状态、模型绑定和 API Key 状态。',
                        model: binding.model,
                        provider: binding.provider,
                        key: apiKey.key_prefix + '...',
                        created_at: new Date().toISOString(),
                    };
                } finally {
                    const elapsed = Date.now() - loadingStartedAt;
                    if (elapsed < 700) await new Promise((resolve) => setTimeout(resolve, 700 - elapsed));
                    this.modelTestLoading = false;
                    this.modelTestProgressText = '';
                }
            } else {
                const model = this.selectedModelTestModel();
                const provider = this.modelTestForm.provider;
                const keySuffix = this.modelTestForm.keySuffix === 'auto'
                    ? (this.modelTestKeysForProvider(provider)[0]?.suffix || 'auto')
                    : this.modelTestForm.keySuffix;
                if (!provider || !this.modelTestForm.model) {
                    this.modelTestResult = {
                        ok: false,
                        title: '配置不完整',
                        detail: '请选择 Provider 和模型后再测试。',
                        suggestion: '如果列表为空，请先在配置管理中添加 Provider、模型和 Key。',
                        created_at: startedAt.toISOString(),
                    };
                    this.addModelTestHistory(this.modelTestResult);
                    return;
                }
                this.modelTestLoading = true;
                this.modelTestProgressText = '\u6b63\u5728\u8fde\u63a5 Provider \u5e76\u53d1\u8d77\u6a21\u578b\u8c03\u7528...';
                this.modelTestResult = null;
                const loadingStartedAt = Date.now();
                try {
                    const data = await this.api('/admin/api/model-tests/run', {
                        method: 'POST',
                        quiet: true,
                        body: JSON.stringify({
                            mode: 'system',
                            provider,
                            model: this.modelTestForm.model,
                            key_suffix: this.modelTestForm.keySuffix,
                            prompt: this.modelTestForm.prompt,
                        }),
                    });
                    this.modelTestResult = this.modelTestResultFromResponse(data, {
                        model: this.modelTestForm.model,
                        provider,
                        base_url: this.providerBaseUrl(provider),
                        key: keySuffix === 'auto' ? '自动选择' : `****${keySuffix}`,
                    });
                } catch (error) {
                    this.modelTestResult = {
                        ok: false,
                        title: '测试请求失败',
                        detail: error.message || '无法完成测试请求',
                        suggestion: '请检查 Provider、模型和 Key 是否配置完整。',
                        model: this.modelTestForm.model,
                        provider,
                        base_url: this.providerBaseUrl(provider),
                        key: keySuffix === 'auto' ? '自动选择' : `****${keySuffix}`,
                        created_at: new Date().toISOString(),
                    };
                } finally {
                    const elapsed = Date.now() - loadingStartedAt;
                    if (elapsed < 700) await new Promise((resolve) => setTimeout(resolve, 700 - elapsed));
                    this.modelTestLoading = false;
                    this.modelTestProgressText = '';
                }
            }
            this.addModelTestHistory(this.modelTestResult);
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
            if (!value) return '-';
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) return String(value).replace('T', ' ').substring(0, 16);
            return date.toLocaleString('zh-CN', {
                timeZone: 'Asia/Shanghai',
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
            });
        },

        gatewayBaseUrl() {
            return this.prototypeWorkflow.gateway_base_url || `${window.location.origin}/v1`;
        },

        primaryUserApiKey() {
            return this.activeUserApiKeys()[0] || null;
        },

        callConfigBinding() {
            const binding = this.prototypeBinding();
            return binding?.status === 'active' ? binding : null;
        },

        callConfigReady() {
            return Boolean(this.callConfigBinding() && this.primaryUserApiKey());
        },

        callConfigMaskedKey() {
            const key = this.primaryUserApiKey();
            if (!key) return '-';
            return this.revealedUserApiKeys[key.id] || `${key.key_prefix}........`;
        },

        async revealPrimaryUserApiKey() {
            const key = this.primaryUserApiKey();
            if (!key) return '';
            if (this.revealedUserApiKeys[key.id]) return this.revealedUserApiKeys[key.id];
            const data = await this.api(`/admin/api/user-api-keys/${key.id}/reveal`);
            this.revealedUserApiKeys = {
                ...this.revealedUserApiKeys,
                [key.id]: data.api_key,
            };
            return data.api_key || '';
        },

        async copyCallConfig(kind) {
            const binding = this.callConfigBinding();
            const key = this.primaryUserApiKey();
            if (!binding || !key) return;
            let text = '';
            let label = '\u5df2\u590d\u5236';
            if (kind === 'key') {
                text = await this.revealPrimaryUserApiKey();
                label = '\u5df2\u590d\u5236 Key';
            } else if (kind === 'endpoint') {
                text = `Base URL: ${this.gatewayBaseUrl()}\nModel: ${binding.alias || binding.model}`;
                label = '\u5df2\u590d\u5236 URL \u548c\u6a21\u578b';
            } else if (kind === 'sample') {
                text = this.callConfigExample(true);
                label = '\u5df2\u590d\u5236\u6837\u4f8b';
            } else {
                const apiKey = await this.revealPrimaryUserApiKey();
                text = [
                    `Base URL: ${this.gatewayBaseUrl()}`,
                    `API Key: ${apiKey}`,
                    `Model: ${binding.alias || binding.model}`,
                    `Actual Model: ${binding.model}`,
                    `Provider: ${binding.provider}`,
                ].join('\n');
                label = '\u5df2\u590d\u5236\u5b8c\u6574\u914d\u7f6e';
            }
            await navigator.clipboard?.writeText(text);
            this.callConfigCopyFeedback = label;
            setTimeout(() => {
                if (this.callConfigCopyFeedback === label) this.callConfigCopyFeedback = '';
            }, 1800);
        },

        callConfigExample(forCopy = false) {
            const binding = this.callConfigBinding();
            const model = binding?.alias || binding?.model || 'your-model-alias';
            const baseUrl = this.gatewayBaseUrl();
            const apiKey = 'YOUR_API_KEY';
            if (this.callConfigExampleTab === 'openai') {
                return [
                    'from openai import OpenAI',
                    '',
                    'client = OpenAI(',
                    `    api_key="${apiKey}",`,
                    `    base_url="${baseUrl}",`,
                    ')',
                    '',
                    'response = client.chat.completions.create(',
                    `    model="${model}",`,
                    '    messages=[{"role": "user", "content": "你好"}],',
                    ')',
                    'print(response.choices[0].message.content)',
                ].join('\n');
            }
            return [
                `curl ${baseUrl}/chat/completions \\`,
                `  -H "Authorization: Bearer ${apiKey}" \\`,
                '  -H "Content-Type: application/json" \\',
                "  -d '{",
                `    "model": "${model}",`,
                '    "messages": [',
                '      {"role": "user", "content": "你好"}',
                '    ]',
                "  }'",
            ].join('\n');
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
                    gateway_base_url: data.gateway_base_url,
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

        async loadUserApiKeys() {
            if (!this.currentUser || this.currentUser.role !== 'user') return;
            try {
                this.userApiKeys = await this.api('/admin/api/user-api-keys/me');
            } catch (e) { console.error(e); }
        },

        async createUserApiKey() {
            try {
                const payload = { name: '\u9ed8\u8ba4\u8c03\u7528 Key' };
                const data = await this.api('/admin/api/user-api-keys', {
                    method: 'POST',
                    body: JSON.stringify(payload),
                });
                this.newUserApiKey = data.api_key || '';
                await this.loadUserApiKeys();
            } catch (e) { console.error(e); }
        },

        async revokeUserApiKey(key) {
            if (!window.confirm(`确认停用「${key.name}」吗？停用后使用该 Key 的调用会失败。`)) return;
            try {
                await this.api(`/admin/api/user-api-keys/${key.id}/revoke`, { method: 'POST' });
                if (this.newUserApiKey && this.newUserApiKey.startsWith(key.key_prefix)) {
                    this.newUserApiKey = '';
                }
                delete this.revealedUserApiKeys[key.id];
                await this.loadUserApiKeys();
            } catch (e) { console.error(e); }
        },

        async revealUserApiKey(key) {
            if (this.revealedUserApiKeys[key.id]) {
                const next = { ...this.revealedUserApiKeys };
                delete next[key.id];
                this.revealedUserApiKeys = next;
                return;
            }
            try {
                const data = await this.api(`/admin/api/user-api-keys/${key.id}/reveal`);
                this.revealedUserApiKeys = {
                    ...this.revealedUserApiKeys,
                    [key.id]: data.api_key,
                };
            } catch (e) { console.error(e); }
        },

        async rotateUserApiKey(key) {
            if (!window.confirm(`确认重新生成「${key.name}」吗？旧 Key 会立即停用。`)) return;
            try {
                const data = await this.api(`/admin/api/user-api-keys/${key.id}/rotate`, { method: 'POST' });
                this.newUserApiKey = data.api_key || '';
                await this.loadUserApiKeys();
            } catch (e) { console.error(e); }
        },

        async logout() {
            this.closeUsageEvents();
            await fetch('/admin/api/auth/logout', { method: 'POST' });
            window.location.href = '/admin/login';
        },

        async toggleProvider(name) {
            this.expandedProvider = name;
            this.selectedKeyProvider = name;
            if (this.selectedKeyProvider && !Object.keys(this.configData || {}).length) {
                await this.loadConfig();
            }
        },

        async loadKeys(options = {}) {
            if (this.currentUser && !this.canAccessModule('keys')) {
                return;
            }
            try {
                this.keysData = await this.api('/v1/keys/status', { quiet: options.quiet });
                const providers = Object.keys(this.keysData || {});
                if (!providers.includes(this.selectedKeyProvider)) {
                    this.selectedKeyProvider = providers[0] || '';
                    this.expandedProvider = this.selectedKeyProvider;
                }
            } catch (e) {
                if (!options.quiet) console.error(e);
            }
        },

        keyProviderNames() {
            return Object.keys(this.keysData || {}).sort();
        },

        selectedKeyPool() {
            return this.keysData?.[this.selectedKeyProvider] || null;
        },

        keyPoolStatus(pool) {
            if (!pool || !pool.total) return 'empty';
            if ((pool.available || 0) <= 0) return 'bad';
            if ((pool.available || 0) < (pool.total || 0)) return 'warn';
            return 'ok';
        },

        keyPoolStatusLabel(pool) {
            const status = this.keyPoolStatus(pool);
            if (status === 'ok') return '\u5168\u90e8\u53ef\u7528';
            if (status === 'warn') return '\u90e8\u5206\u53ef\u7528';
            if (status === 'bad') return '\u4e0d\u53ef\u7528';
            return '\u672a\u914d\u7f6e Key';
        },

        keyPoolStatusClass(pool) {
            const status = this.keyPoolStatus(pool);
            if (status === 'ok') return 'status-ok';
            if (status === 'warn') return 'status-warn';
            return 'status-bad';
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
                        { label: '\u6210\u672c (RMB)', data: costs, backgroundColor: '#10b981' },
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

        logQueryString() {
            const params = new URLSearchParams({
                limit: '50',
                offset: String(this.logsOffset),
            });
            for (const [key, value] of Object.entries(this.logFilters || {})) {
                if (!value) continue;
                params.set(key, value);
            }
            return params.toString();
        },

        async applyLogFilters() {
            this.logsOffset = 0;
            await this.loadLogs();
        },

        async resetLogFilters() {
            this.logFilters = {
                status: '',
                provider: '',
                model: '',
                username: '',
                start: '',
                end: '',
            };
            this.logsOffset = 0;
            await this.loadLogs();
        },

        logIdentity(log) {
            return log?.request_id || log?.timestamp || '';
        },

        toggleLogDetail(log) {
            const id = this.logIdentity(log);
            this.expandedLog = this.expandedLog === id ? null : id;
        },

        async loadLogs(options = {}) {
            try {
                const data = await this.api('/v1/logs?' + this.logQueryString(), { quiet: options.quiet });
                if (this.logsOffset === 0) {
                    this.logsData = data;
                    this.expandedLog = null;
                } else {
                    this.logsData.items = [...this.logsData.items, ...data.items];
                    this.logsData.total = data.total;
                }
            } catch (e) {
                if (!options.quiet) console.error(e);
            }
        },

        async loadAuditLogs() {
            try {
                const data = await this.api('/admin/api/audit-logs?limit=100&offset=' + this.auditOffset);
                if (this.auditOffset === 0) {
                    this.auditData = data;
                    this.expandedAuditLog = null;
                } else {
                    this.auditData.items = [...this.auditData.items, ...data.items];
                    this.auditData.total = data.total;
                }
            } catch (e) { console.error(e); }
        },

        toggleAuditDetail(log) {
            this.expandedAuditLog = this.expandedAuditLog === log.id ? null : log.id;
        },

        auditActionLabel(action) {
            return {
                model_request_create: '\u63d0\u4ea4\u6a21\u578b\u7533\u8bf7',
                model_request_approve: '\u901a\u8fc7\u6a21\u578b\u7533\u8bf7',
                model_request_reject: '\u62d2\u7edd\u6a21\u578b\u7533\u8bf7',
                model_binding_stop: '\u505c\u6b62\u6a21\u578b\u4f7f\u7528',
                model_binding_resume: '\u6062\u590d\u6a21\u578b\u4f7f\u7528',
                user_api_key_create: '\u751f\u6210 API Key',
                user_api_key_revoke: '\u505c\u7528 API Key',
                user_api_key_rotate: '\u91cd\u65b0\u751f\u6210 API Key',
                user_create: '\u65b0\u589e\u7528\u6237',
                user_update: '\u66f4\u65b0\u7528\u6237',
                user_delete: '\u5220\u9664\u7528\u6237',
                registration_approve: '通过注册申请',
                registration_reject: '拒绝注册申请',
                registration_code_reset: '重置注册码',
                config_update: '\u4fdd\u5b58\u914d\u7f6e',
            }[action] || action;
        },

        formatLogTime(value) {
            if (!value) return '-';
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) return String(value).substring(11, 19) || '-';
            return date.toLocaleString('zh-CN', {
                timeZone: 'Asia/Shanghai',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false,
            });
        },

        async loadRegistrationPrototype() {
            const data = await this.api('/admin/api/registration-requests');
            this.registrationRequests = data.items || [];
        },

        registrationStatusLabel(status) {
            return {
                pending: '待审核',
                approved: '已通过',
                rejected: '已拒绝',
                registered: '已注册',
            }[status] || status || '-';
        },

        registrationStatusClass(status) {
            if (status === 'approved' || status === 'registered') return 'status-ok';
            if (status === 'rejected') return 'status-bad';
            return 'status-warn';
        },

        async approveRegistrationRequest(id) {
            const data = await this.api(`/admin/api/registration-requests/${id}/approve`, { method: 'POST' });
            const request = data.request;
            await this.loadRegistrationPrototype();
            this.registrationFeedback = `已通过 ${request.target}，注册码：${request.invite_code}`;
        },

        async rejectRegistrationRequest(id) {
            const data = await this.api(`/admin/api/registration-requests/${id}/reject`, { method: 'POST' });
            const request = data.request;
            await this.loadRegistrationPrototype();
            this.registrationFeedback = `已拒绝 ${request.target} 的注册申请`;
        },

        async resetRegistrationCode(id) {
            const data = await this.api(`/admin/api/registration-requests/${id}/reset-code`, { method: 'POST' });
            const request = data.request;
            await this.loadRegistrationPrototype();
            this.registrationFeedback = `已重新生成注册码：${request.invite_code}`;
        },

        async copyRegistrationCode(request) {
            if (!request?.invite_code) return;
            await navigator.clipboard?.writeText(request.invite_code);
            this.registrationFeedback = `已复制 ${request.target} 的注册码`;
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
                const providers = this.providerNames();
                if (!providers.includes(this.configProviderSelection)) {
                    this.configProviderSelection = providers[0] || '';
                }
            } catch (e) { console.error(e); }
        },

        async saveConfig(options = {}) {
            try {
                const summary = this.configChangeSummary();
                if (options.confirm !== false && !window.confirm(summary)) return;
                await this.persistConfig();
            } catch (e) { console.error(e); }
        },

        async persistConfig() {
            await this.api('/v1/config', {
                method: 'PUT',
                body: JSON.stringify(this.configData),
            });
            this.configSaved = true;
            setTimeout(() => { this.configSaved = false; }, 3000);
            await this.loadConfig();
            await this.loadKeys();
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

        async deleteAlias(alias) {
            const model = this.configData.aliases?.[alias] || '-';
            if (!this.confirmDelete(`确认删除模型别名「${alias} -> ${model}」吗？`)) return;
            this.removeModelReferences(alias, model);
            delete this.configData.aliases[alias];
            await this.persistConfig();
        },

        removeModelReferences(alias, model) {
            const targets = new Set([alias, model].filter(Boolean));
            this.configData.routes = (this.configData.routes || []).filter((route) => !targets.has(route.pattern));

            for (const [provider, models] of Object.entries(this.configData.pricing || {})) {
                for (const target of targets) {
                    if (models && Object.prototype.hasOwnProperty.call(models, target)) {
                        delete models[target];
                    }
                }
                if (models && Object.keys(models).length === 0) {
                    this.configData.pricing[provider] = {};
                }
            }

            for (const keypool of Object.values(this.configData.keypools || {})) {
                keypool.keys = (keypool.keys || []).map((item) => {
                    if (typeof item !== 'object' || item === null || !Array.isArray(item.allowed_models)) {
                        return item;
                    }
                    const allowed = item.allowed_models.filter((value) => !targets.has(value));
                    if (allowed.length === 0) return null;
                    return { ...item, allowed_models: allowed };
                }).filter(Boolean);
            }
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
                ...Object.keys(this.configData.provider_base_urls || {}),
                ...Object.keys(this.configData.pricing || {}),
                ...(this.configData.routes || []).map((route) => route.provider).filter(Boolean),
            ])).sort();
        },

        addProviderBaseUrl() {
            this.configData.provider_base_urls = this.configData.provider_base_urls || {};
            let index = 1;
            let name = 'new_provider';
            while (Object.prototype.hasOwnProperty.call(this.configData.provider_base_urls, name)) {
                name = `new_provider_${index}`;
                index += 1;
            }
            this.configData.provider_base_urls[name] = '';
            this.configProviderSelection = name;
        },

        deleteProviderBaseUrl(name) {
            if (!this.confirmDelete(`确认删除 Provider「${name}」的 Base URL 吗？`)) return;
            delete this.configData.provider_base_urls[name];
            if (this.configProviderSelection === name) {
                this.configProviderSelection = this.providerNames()[0] || '';
            }
        },

        providerConfigCards() {
            return this.providerNames().map((name) => {
                const keypool = this.configData.keypools?.[name] || {};
                const keys = keypool.keys || [];
                const models = this.providerModels(name);
                const baseUrl = this.providerBaseUrl(name);
                const routeCount = (this.configData.routes || []).filter((route) => route.provider === name).length;
                const status = !baseUrl ? 'warning' : keys.length ? 'ok' : models.length || routeCount ? 'warning' : 'neutral';
                return {
                    name,
                    baseUrl,
                    keyCount: keys.length,
                    modelCount: models.length,
                    routeCount,
                    strategy: keypool.strategy || '-',
                    rateLimit: keypool.rate_limit || '-',
                    status,
                    statusLabel: status === 'ok' ? '已配置' : status === 'warning' ? '需完善' : '空配置',
                };
            });
        },

        selectConfigProvider(name) {
            this.configProviderSelection = name;
        },

        selectedProviderConfig() {
            return this.providerConfigCards().find((provider) => provider.name === this.configProviderSelection)
                || this.providerConfigCards()[0]
                || null;
        },

        selectedProviderPricing() {
            const provider = this.selectedProviderConfig();
            if (!provider) return [];
            return Object.entries(this.configData.pricing?.[provider.name] || {}).map(([model, price]) => ({
                model,
                input: Number(price.input || 0),
                output: Number(price.output || 0),
                cachedInput: Number(price.cached_input || 0),
                context: price.context || '-',
                currency: price.currency || 'CNY',
            }));
        },

        providerCardClass(provider) {
            if (provider.status === 'ok') return 'provider-card-green';
            if (provider.status === 'warning') return 'provider-card-yellow';
            return 'provider-card-red';
        },

        editProviderFromCard(name) {
            this.configTab = 'keypools';
            this.editKeyPool(name);
        },

        keypoolCards() {
            return Object.entries(this.configData.keypools || {}).map(([name, keypool]) => {
                const keys = keypool.keys || [];
                const scopedModels = [];
                let allModelKeys = 0;
                for (const key of keys) {
                    const allowed = this.keyAllowedModels(key);
                    if (!allowed.length) allModelKeys += 1;
                    scopedModels.push(...allowed);
                }
                const models = [...new Set(scopedModels)];
                return {
                    name,
                    keys,
                    keyCount: keys.length,
                    strategy: keypool.strategy || 'round-robin',
                    rateLimit: keypool.rate_limit || '-',
                    models,
                    allModelKeys,
                    status: keys.length ? 'ok' : 'warning',
                    statusLabel: keys.length ? '已配置 Key' : '未配置 Key',
                };
            }).sort((a, b) => a.name.localeCompare(b.name));
        },

        exactModelPattern(pattern) {
            return Boolean(pattern) && !String(pattern).includes('*');
        },

        knownConfigModels() {
            const models = new Set();
            for (const [alias, model] of Object.entries(this.configData.aliases || {})) {
                if (alias) models.add(alias);
                if (model) models.add(model);
            }
            for (const route of this.configData.routes || []) {
                if (this.exactModelPattern(route.pattern)) models.add(route.pattern);
            }
            for (const modelMap of Object.values(this.configData.pricing || {})) {
                for (const model of Object.keys(modelMap || {})) models.add(model);
            }
            return models;
        },

        configHealthItems() {
            const items = [];
            const providers = new Set(this.providerNames());
            const models = this.knownConfigModels();
            const add = (level, title, detail) => items.push({ level, title, detail });

            for (const [index, route] of (this.configData.routes || []).entries()) {
                if (!route.pattern) add('error', `路由 ${index + 1} 缺少 Pattern`, '保存配置前需要补齐或删除这条路由。');
                if (!route.provider) add('error', `路由 ${index + 1} 缺少 Provider`, `Pattern: ${route.pattern || '-'}`);
                else if (!providers.has(route.provider)) add('error', `路由指向不存在的 Provider：${route.provider}`, `Pattern: ${route.pattern || '-'}`);
            }

            for (const [alias, model] of Object.entries(this.configData.aliases || {})) {
                if (!alias) add('error', '存在空的模型别名', '请填写别名或删除该行。');
                if (!model) add('error', `模型别名 ${alias || '-'} 缺少实际模型`, '该模型无法被路由解析。');
                else if (!this.inferProviderForModel(model)) add('warning', `模型 ${alias} 暂未匹配到 Provider`, `实际模型: ${model}`);
            }

            for (const provider of providers) {
                const baseUrl = this.providerBaseUrl(provider);
                const keypool = this.configData.keypools?.[provider];
                const keyCount = (keypool?.keys || []).length;
                const hasModel = Object.entries(this.configData.aliases || {}).some(([, model]) => this.inferProviderForModel(model) === provider)
                    || (this.configData.routes || []).some((route) => route.provider === provider && route.pattern !== '*');
                if (!baseUrl) add('warning', `Provider ${provider} 缺少 Base URL`, '如果它不是内置默认 Provider，请补齐 Base URL。');
                if (hasModel && keyCount === 0) add('warning', `Provider ${provider} 没有 Key`, '相关模型会展示出来，但调用时不可用。');
            }

            for (const [provider, keypool] of Object.entries(this.configData.keypools || {})) {
                for (const [index, key] of (keypool.keys || []).entries()) {
                    if (typeof key !== 'object' || key === null) continue;
                    for (const model of key.allowed_models || []) {
                        if (!models.has(model)) add('error', `${provider} 第 ${index + 1} 个 Key 绑定了不存在的模型`, model);
                    }
                }
            }

            if (!items.length) add('ok', '配置体检通过', '当前没有发现阻断保存的问题。');
            return items;
        },

        configHealthCounts() {
            const items = this.configHealthItems();
            return {
                errors: items.filter((item) => item.level === 'error').length,
                warnings: items.filter((item) => item.level === 'warning').length,
                ok: items.filter((item) => item.level === 'ok').length,
                models: Object.keys(this.configData.aliases || {}).length,
                providers: this.providerNames().length,
            };
        },

        pricingPrototypeRows() {
            const catalog = [
                {
                    providerLabel: 'volcengine',
                    providers: ['volcengine'],
                    model: 'ark-code-latest',
                    input: 3.2,
                    output: 16.0,
                    cachedInput: 0.64,
                    context: '256K',
                },
                {
                    providerLabel: 'qwen',
                    providers: ['qwen'],
                    model: 'qwen-plus',
                    input: 0.8,
                    output: 2.0,
                    cachedInput: 0.16,
                    context: '128K',
                },
                {
                    providerLabel: 'openai / psydo',
                    providers: ['openai', 'psydo'],
                    model: 'gpt-5.4',
                    input: 18.0,
                    output: 108.0,
                    cachedInput: 0.25,
                    context: '1M',
                },
                {
                    providerLabel: 'volcengine / deepseek',
                    providers: ['volcengine', 'deepseek'],
                    model: 'deepseek-r1',
                    input: 9.6,
                    output: 38.4,
                    cachedInput: 1.92,
                    context: '64K',
                },
            ];
            return catalog.map((row) => {
                const configured = row.providers.some((provider) => {
                    const pricing = this.configData.pricing?.[provider]?.[row.model];
                    return pricing
                        && Number(pricing.input) === row.input
                        && Number(pricing.output) === row.output
                        && Number(pricing.cached_input || 0) === row.cachedInput
                        && String(pricing.context || '') === row.context
                        && String(pricing.currency || 'CNY') === 'CNY';
                });
                return {
                    ...row,
                    currency: 'CNY',
                    configured,
                    statusLabel: configured ? '\u5df2\u914d\u7f6e' : '\u5f85\u5199\u5165',
                    statusClass: configured ? 'status-ok' : 'status-warn',
                };
            });
        },

        pricingPrototypeSummary() {
            const rows = this.pricingPrototypeRows();
            return {
                total: rows.length,
                configured: rows.filter((row) => row.configured).length,
            };
        },

        openProviderWizard() {
            const firstModel = this.modelAccessOptions()[0];
            this.providerWizard = {
                selectedModel: firstModel?.id || '',
                name: firstModel?.provider || '',
                baseUrl: firstModel ? this.providerBaseUrl(firstModel.provider) : '',
                routePattern: firstModel?.model || '',
                strategy: 'round-robin',
                rateLimit: firstModel?.rateLimit || 60,
                key: '',
            };
            this.showProviderWizard = true;
        },

        modelAccessOptions() {
            const options = [];
            const seen = new Set();
            const add = (alias, model, source = 'alias') => {
                const value = model || alias;
                if (!value) return;
                const id = `${alias || value}::${value}`;
                if (seen.has(id)) return;
                seen.add(id);
                const provider = this.inferProviderForModel(value);
                const keypool = this.configData.keypools?.[provider] || {};
                const keyCount = (keypool.keys || []).length;
                options.push({
                    id,
                    alias: alias || value,
                    model: value,
                    provider,
                    source,
                    keyCount,
                    hasKeys: keyCount > 0,
                    rateLimit: keypool.rate_limit || 60,
                    strategy: keypool.strategy || 'round-robin',
                    label: `${alias || value} / ${value} / ${provider || '未路由'} / ${keyCount > 0 ? `已配置 ${keyCount} 个 Key` : '未配置 Key'}`,
                });
            };

            for (const [alias, model] of Object.entries(this.configData.aliases || {})) {
                add(alias, model, 'alias');
            }
            for (const route of this.configData.routes || []) {
                if (route.pattern && route.pattern !== '*' && !route.pattern.includes('*')) {
                    add(route.pattern, route.pattern, 'route');
                }
            }

            return options.sort((a, b) => {
                if (a.hasKeys !== b.hasKeys) return a.hasKeys ? 1 : -1;
                return `${a.alias}-${a.model}`.localeCompare(`${b.alias}-${b.model}`);
            });
        },

        selectedProviderWizardModel() {
            return this.modelAccessOptions().find((item) => item.id === this.providerWizard.selectedModel) || null;
        },

        inferProviderForModel(model) {
            const routes = this.configData.routes || [];
            for (const route of routes) {
                if (route.pattern && route.pattern !== '*' && this.routeMatchesModel(route.pattern, model)) {
                    return route.provider;
                }
            }
            const lowerModel = String(model || '').toLowerCase();
            const names = this.providerNames().sort((a, b) => b.length - a.length);
            return names.find((name) => lowerModel.startsWith(String(name).toLowerCase())) || this.providerForModel(model);
        },

        syncProviderWizardFromModel() {
            const item = this.selectedProviderWizardModel();
            if (!item) return;
            this.providerWizard.name = item.provider || '';
            this.providerWizard.baseUrl = item.provider ? this.providerBaseUrl(item.provider) : '';
            this.providerWizard.routePattern = item.model || item.alias || '';
            this.providerWizard.rateLimit = item.rateLimit || 60;
            this.providerWizard.strategy = item.strategy || 'round-robin';
        },

        async saveProviderWizard() {
            let name = this.providerWizard.name.trim().toLowerCase();
            const baseUrl = this.providerWizard.baseUrl.trim();
            const routePattern = this.providerWizard.routePattern.trim();
            const key = this.providerWizard.key.trim();
            const rateLimit = Number(this.providerWizard.rateLimit) || 60;
            const strategy = this.providerWizard.strategy || 'round-robin';
            const selectedModel = this.selectedProviderWizardModel();

            if (
                selectedModel
                && name
                && [selectedModel.alias, selectedModel.model, routePattern].map((value) => String(value || '').toLowerCase()).includes(name)
                && selectedModel.provider
            ) {
                name = selectedModel.provider;
                this.providerWizard.name = selectedModel.provider;
            }

            if (!/^[a-z0-9_-]+$/.test(name)) {
                window.alert('Provider 通道名称只能填写 qwen、openai、volcengine 这类供应商通道名；qwen3.6_plus 这种模型名请填写到“路由匹配模型”。');
                return;
            }
            if (!baseUrl) {
                window.alert('请填写 Base URL。');
                return;
            }
            this.configData.provider_base_urls = this.configData.provider_base_urls || {};
            this.configData.keypools = this.configData.keypools || {};
            this.configData.pricing = this.configData.pricing || {};

            this.configData.provider_base_urls[name] = baseUrl;
            const existingKeys = this.configData.keypools[name]?.keys || [];
            const existingValues = existingKeys.map((item) => this.keyValue(item));
            this.configData.keypools[name] = {
                keys: key && !existingValues.includes(key) ? [...existingKeys, { value: key, allowed_models: routePattern ? [routePattern] : [] }] : existingKeys,
                strategy,
                rate_limit: rateLimit,
            };
            this.configData.pricing[name] = this.configData.pricing[name] || {};
            if (routePattern) this.ensureModelRoute(routePattern, name);
            this.showProviderWizard = false;
            await this.persistConfig();
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

        async saveModelWizard() {
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
            await this.persistConfig();
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

        toggleEditingKeyModel(index, model) {
            const item = this.editKeyPoolKeys[index];
            if (!item || !model) return;
            item.allowed_models = item.allowed_models || [];
            if (item.allowed_models.includes(model)) {
                item.allowed_models = item.allowed_models.filter((value) => value !== model);
            } else {
                item.allowed_models.push(model);
            }
        },

        editingKeyCoversAllModels(key) {
            return !key.allowed_models || key.allowed_models.length === 0;
        },

        editKeyPool(name) {
            this.editingKeyPool = name;
            this.editKeyPoolBaseUrl = this.providerBaseUrl(name);
            this.editKeyPoolKeys = [...(this.configData.keypools?.[name]?.keys || [])].map((item) => this.normalizeEditableKey(item));
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
                this.editKeyPoolKeys = [...(data.keys || [])].map((item) => this.normalizeEditableKey(item));
                this.plainKeysVisible = true;
            } catch (e) { console.error(e); }
        },

        async saveKeyPool() {
            if (this.editingKeyPool && this.configData.keypools) {
                const savingProvider = this.editingKeyPool;
                this.configData.provider_base_urls = this.configData.provider_base_urls || {};
                const baseUrl = this.editKeyPoolBaseUrl.trim();
                if (baseUrl) this.configData.provider_base_urls[savingProvider] = baseUrl;
                else delete this.configData.provider_base_urls[savingProvider];

                const original = this.configData.keypools[savingProvider]?.keys || [];
                const merged = this.editKeyPoolKeys.map((key, i) => {
                    const value = (key.value || '').trim();
                    const allowedModels = [...new Set(key.allowed_models || [])].filter(Boolean);
                    if (value === '****' && i < original.length) {
                        const originalItem = original[i];
                        if (typeof originalItem === 'object' && originalItem !== null) {
                            return { value: '****', allowed_models: allowedModels };
                        }
                        return allowedModels.length ? { value: '****', allowed_models: allowedModels } : originalItem;
                    }
                    if (!value) return null;
                    return allowedModels.length ? { value, allowed_models: allowedModels } : value;
                }).filter(Boolean);
                this.configData.keypools[savingProvider].keys = merged;
                await this.persistConfig();
            }
            this.editingKeyPool = null;
            this.editKeyPoolBaseUrl = '';
            this.editKeyPoolSelectedModel = '';
            this.plainKeysVisible = false;
        },
    };
}
