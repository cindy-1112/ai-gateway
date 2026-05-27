function authApp() {
    return {
        mode: 'login',
        registerMode: 'apply',
        registerType: 'email',
        message: '',
        username: '',
        loginPassword: '',
        email: '',
        phone: '',
        password: '',
        code: '',
        requestReason: '',
        inviteTarget: '',
        inviteCode: '',
        sendingCode: false,

        showPending(text) {
            this.message = text;
            window.setTimeout(() => {
                if (this.message === text) {
                    this.message = '';
                }
            }, 3000);
        },

        passwordStrengthError(password) {
            if (password.length < 10) return '密码至少需要 10 位';
            if (/\s/.test(password)) return '密码不能包含空格';
            if (!/[A-Za-z]/.test(password)) return '密码必须包含至少 1 个字母';
            if (!/\d/.test(password)) return '密码必须包含至少 1 个数字';
            return '';
        },

        async sendVerificationCode() {
            const target = this.registerType === 'email' ? this.email : this.phone;
            if (!target.trim()) {
                this.showPending(this.registerType === 'email' ? '请先输入邮箱' : '请先输入手机号');
                return;
            }

            this.sendingCode = true;
            try {
                const response = await fetch('/admin/api/auth/verification-code', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        channel: this.registerType,
                        target,
                    }),
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.detail || '验证码发送失败');
                }
                this.message = `验证码已生成：${data.debug_code}，5 分钟内有效`;
            } catch (error) {
                this.message = error.message;
            } finally {
                this.sendingCode = false;
            }
        },

        async login() {
            if (!this.username.trim() || !this.loginPassword) {
                this.showPending('请输入账号和密码');
                return;
            }

            try {
                const response = await fetch('/admin/api/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        username: this.username,
                        password: this.loginPassword,
                    }),
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.detail || '登录失败');
                }
                window.location.href = '/admin/';
            } catch (error) {
                this.message = error.message;
            }
        },

        async register() {
            const target = this.registerType === 'email' ? this.email : this.phone;
            if (!target.trim()) {
                this.showPending(this.registerType === 'email' ? '请先输入邮箱' : '请先输入手机号');
                return;
            }
            const passwordError = this.passwordStrengthError(this.password);
            if (passwordError) {
                this.showPending(passwordError);
                return;
            }
            if (!this.code.trim()) {
                this.showPending('请先输入验证码');
                return;
            }

            try {
                const response = await fetch('/admin/api/auth/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        channel: this.registerType,
                        target,
                        password: this.password,
                        code: this.code,
                    }),
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.detail || '注册失败');
                }
                this.message = `注册成功：${data.username}，下一阶段将接入登录`;
                this.mode = 'login';
            } catch (error) {
                this.message = error.message;
            }
        },

        async submitRegistrationRequest() {
            const target = this.registerType === 'email' ? this.email.trim() : this.phone.trim();
            if (!target) {
                this.showPending(this.registerType === 'email' ? '请先输入邮箱' : '请先输入手机号');
                return;
            }

            try {
                const response = await fetch('/admin/api/auth/registration-requests', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        channel: this.registerType,
                        target,
                        reason: this.requestReason.trim(),
                    }),
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.detail || '注册申请提交失败');
                }
                const request = data.request || {};
                this.message = request.status === 'approved'
                    ? '申请已通过，请向管理员线下获取注册码'
                    : '注册申请已提交，请等待管理员审核并线下获取注册码';
                this.registerMode = 'invite';
                this.inviteTarget = request.target || target;
            } catch (error) {
                this.message = error.message;
            }
        },

        async completeInviteRegistration() {
            const target = this.inviteTarget.trim();
            const code = this.inviteCode.trim().toUpperCase();
            if (!target) {
                this.showPending('请输入申请时使用的邮箱或手机号');
                return;
            }
            const passwordError = this.passwordStrengthError(this.password);
            if (passwordError) {
                this.showPending(passwordError);
                return;
            }
            if (!code) {
                this.showPending('请输入管理员发放的一次性注册码');
                return;
            }

            try {
                const response = await fetch('/admin/api/auth/register-with-code', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        target,
                        password: this.password,
                        code,
                    }),
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.detail || '注册失败');
                }
                this.message = `注册成功：${data.username}，请使用新账号登录。`;
                this.mode = 'login';
                this.username = data.username || target;
            } catch (error) {
                this.message = error.message;
            }
        },
    };
}
