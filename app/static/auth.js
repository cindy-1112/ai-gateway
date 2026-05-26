function authApp() {
    return {
        mode: 'login',
        registerType: 'email',
        message: '',
        username: '',
        loginPassword: '',
        email: '',
        phone: '',
        password: '',
        code: '',
        sendingCode: false,

        showPending(text) {
            this.message = text;
            window.setTimeout(() => {
                if (this.message === text) {
                    this.message = '';
                }
            }, 3000);
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
            if (this.password.length < 8) {
                this.showPending('密码至少需要 8 位');
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
    };
}
