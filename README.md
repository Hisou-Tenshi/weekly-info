# weekly-info

一个运行在 **GitHub Actions + Vercel** 的 Journal Club 周报邮件系统，支持：

- 每周二 JST 12:00 定时执行发送任务
- 成员按队列轮换（发送后移至队尾）
- 邮件正文/主题/轮换锚点周三可在前端配置
- 敏感业务配置采用 **加密存储**（公开仓库仅保存密文）
- 管理面板密码哈希校验 + 登录会话 + 节流/锁定防暴力破解

---

## 1. 项目结构

- `journal_club.py`：邮件发送主逻辑（GitHub Actions 调用）
- `weekly_core.py`：SMTP 发送封装
- `state.json`：公开状态（跳过周数、当前轮换状态）
- `secure_config.json`：加密业务配置（成员/模板/锚点/主题）
- `index.html`：Vercel 托管的管理前端页面
- `api/`：Vercel Serverless API
  - `api/secure-config.js`：加密配置读写
  - `api/state.js`：状态查询（含下次发送时间推算）
  - `api/skip.js`：设置跳过周数
  - `api/_security.js`：密码哈希校验、节流、锁定、加解密
  - `api/_github.js`：通过 GitHub Contents API 读写仓库文件
- `.github/workflows/weekly_mail.yml`：定时任务

---

## 2. 运行架构

### 2.1 发送链路

1. GitHub Actions 按计划触发（UTC `0 3 * * 2`，即 JST 周二 12:00）。
2. 执行 `python journal_club.py`。
3. 脚本读取：
   - `state.json`（公开状态）
   - `secure_config.json`（密文），并用私钥解密为明文配置
4. 根据规则决定是否发送并选择讲者/日期。
5. 调用 SMTP 发送。
6. 更新 `state.json`（如 skip 递减、轮换队列变更）。
7. workflow 自动提交 `state.json` 变更回仓库。

### 2.2 管理链路

1. 用户访问 Vercel 上的 `index.html`。
2. 输入管理密码并登录（前端会话默认 30 分钟）。
3. 前端调用 `/api/*`：
   - `/api/secure-config`：读取/保存加密配置
   - `/api/skip`：设置跳过周数
   - `/api/state`：读取当前状态、预估下次发送时间
4. API 在服务端完成密码哈希校验与节流/锁定，且在服务端执行加解密与 GitHub 写回。

---

## 3. 核心业务规则

- **发送频率**：每周二执行一次发送流程。
- **轮换周期**：成员与“对应周三日期”按 14 天一个周期变化。
- **成员队列**：
  - 初始按字母序；
  - 周期首次发送成功后，当前讲者移动到队尾。
- **跳过周数**：
  - `skip_weeks_remaining > 0` 时本周不发送并减 1；
  - 可通过前端设置（`/api/skip`）。

---

## 4. 安全设计

### 4.1 密码与认证

- 不再使用明文 `PANEL_PASSWORD`，改用 `PANEL_PASSWORD_HASH`（SHA-256 hex）。
- API 使用常量时间比较进行校验。
- 前后端均有节流机制，服务端有失败锁定窗口。

### 4.2 机密配置加密

- 使用混合加密：`RSA-OAEP-SHA256 + AES-256-GCM`。
- 仓库中仅有 `secure_config.json` 密文，不存业务明文配置。
- 加解密在服务端进行；前端不会读取私钥。

### 4.3 状态文件可公开

- `state.json` 仅存轮换状态和 skip 计数，不存成员/模板/密钥。

---

## 5. 环境变量配置

建议分别在 **Vercel Project Env**（管理 API 用）和 **GitHub Actions Secrets**（定时发送用）中配置。

### 5.1 公共/发送相关

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASS`
- `FROM_EMAIL`
- `JC_TO`（收件人邮箱组，逗号分隔）

### 5.2 Vercel API 相关

- `GITHUB_TOKEN`（可读写当前仓库）
- `GITHUB_REPO`（格式：`owner/repo`）
- `GITHUB_BRANCH`（可选，默认 `main`）
- `STATE_PATH`（可选，默认 `state.json`）
- `SECURE_CONFIG_PATH`（可选，默认 `secure_config.json`）

### 5.3 安全相关

- `PANEL_PASSWORD_HASH`（管理密码的 SHA-256 hex）
- `JC_RSA_PUBLIC_KEY_PEM`（公钥，`\n` 形式换行）
- `JC_RSA_PRIVATE_KEY_PEM`（私钥，`\n` 形式换行）

> `JC_MEMBERS / JC_TEMPLATE / JC_START_WED / JC_SUBJECT` 已迁移为加密配置，不再要求放入 `.env`。

---

## 6. 快速初始化

### 6.1 安装依赖（本地调试可选）

```bash
pip install -r requirements.txt
```

### 6.2 生成管理密码哈希（PowerShell）

```powershell
[System.BitConverter]::ToString(
  [System.Security.Cryptography.SHA256]::Create().ComputeHash(
    [System.Text.Encoding]::UTF8.GetBytes("your-password")
  )
).Replace("-","").ToLower()
```

将输出填入 `PANEL_PASSWORD_HASH`。

### 6.3 生成 RSA 密钥对（示例）

```bash
openssl genrsa -out private.pem 2048
openssl rsa -in private.pem -pubout -out public.pem
```

把 PEM 内容写入环境变量（换行改为 `\n`）。

### 6.4 初始化加密配置

1. 部署 Vercel 后打开首页。
2. 输入密码登录。
3. 在页面填写：
   - 成员名单（每行一人）
   - 邮件主干模板
   - 轮换锚点周三
   - 可选主题模板
4. 点击“加密保存成员与模板”。

---

## 7. 前端使用说明

- **登录**：输入密码后点击“登录（会话 30 分钟）”。
- **刷新状态**：查看 skip 剩余和下次发送时间。
- **提交跳过周数**：设置 `skip_weeks_remaining`。
- **编辑并保存加密配置**：写入 `secure_config.json` 密文。
- **退出登录**：立即清除本地会话。

---

## 8. GitHub Actions 说明

workflow 文件：`.github/workflows/weekly_mail.yml`

- 定时：JST 周二 12:00
- 手动触发：`workflow_dispatch`
- 任务内容：
  - 安装 Python 依赖
  - 执行 `journal_club.py`
  - 若 `state.json` 有变更则自动 commit + push

---

## 9. 常见问题（Troubleshooting）

- **401 Unauthorized**
  - 检查 `PANEL_PASSWORD_HASH` 是否正确；
  - 确认输入密码是否与生成哈希时一致。

- **429 Too many requests**
  - 触发了节流/锁定，等待 `retry_after_ms` 后再试。

- **解密失败**
  - 检查 `JC_RSA_PUBLIC_KEY_PEM` / `JC_RSA_PRIVATE_KEY_PEM` 是否匹配；
  - 检查是否正确使用 `\n` 存储换行。

- **无法写回仓库**
  - 检查 `GITHUB_TOKEN` 权限；
  - 检查 `GITHUB_REPO`、`GITHUB_BRANCH` 是否正确；
  - 确认仓库开启了允许 Actions/Token 写入对应分支。

- **邮件未发出**
  - 检查 SMTP 变量；
  - 检查 `JC_TO` 是否为空；
  - 查看 Actions 日志确认是否被 skip。

---

## 10. 安全建议

- 定期轮换：
  - `SMTP_PASS`
  - `GITHUB_TOKEN`
  - `PANEL_PASSWORD_HASH` 对应密码
  - RSA 密钥对
- 开启仓库分支保护，限制直接推送。
- 不在本地或仓库中保留明文密钥/密码历史。

---

## 11. 版本提示

当前实现依赖：

- Python 3.11+
- `flask>=3.0.0`
- `cryptography>=45.0.0`