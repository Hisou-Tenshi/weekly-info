# weekly-info

一个运行在 **GitHub Actions + Vercel** 的 Journal Club 周报邮件系统，支持：

- 每周二 JST 12:00 定时执行发送任务
- 成员按队列轮换（发送后移至队尾）
- 邮件正文/主题/轮换锚点周三可在前端配置
- 敏感业务配置采用 **加密存储**（公开仓库仅保存密文）
- 管理面板密码哈希校验 + 登录会话 + 节流/锁定防暴力破解

---

## 0. 超详细部署（照着点就行）

下面是最傻瓜的一次性部署流程，按顺序做，不要跳步。

### 第 0 步：准备 4 样东西

- 一个 GitHub 仓库（就是当前仓库）
- 一个 Vercel 账号
- 可用 SMTP 邮箱账号（主机、端口、用户名、密码、发件人）
- 一条你自己记得住的管理密码（用于前端登录）

### 第 1 步：把代码推到 GitHub

在本地仓库执行：

```bash
git add .
git commit -m "setup weekly-info"
git push
```

> 如果你已经推过，跳过这步。

### 第 2 步：在本地生成密码哈希

打开 PowerShell，执行（把 `your-password` 换成你的实际密码）：

```powershell
[System.BitConverter]::ToString(
  [System.Security.Cryptography.SHA256]::Create().ComputeHash(
    [System.Text.Encoding]::UTF8.GetBytes("your-password")
  )
).Replace("-","").ToLower()
```

复制输出结果，后面要填到 `PANEL_PASSWORD_HASH`。

### 第 3 步：生成 RSA 密钥对

在本地执行：

```bash
openssl genrsa -out private.pem 2048
openssl rsa -in private.pem -pubout -out public.pem
```

得到两个文件：

- `private.pem`（私钥，严禁泄露）
- `public.pem`（公钥）

### 第 4 步：导入 Vercel 项目

1. 打开 Vercel -> `Add New...` -> `Project`
2. 选择当前 GitHub 仓库 `weekly-info`
3. Framework 保持默认（静态 + serverless 即可）
4. 点 `Deploy`

等首次部署成功，先不要关页面。

### 第 5 步：在 Vercel 配环境变量（管理前端/API 用）

进入 Vercel 项目 -> `Settings` -> `Environment Variables`，逐个添加：

- `GITHUB_TOKEN` = 你的 GitHub PAT（要有仓库读写权限）
- `GITHUB_REPO` = `你的GitHub用户名/weekly-info`
- `GITHUB_BRANCH` = `main`（如果你主分支不是 main 就填真实分支）
- `STATE_PATH` = `state.json`
- `SECURE_CONFIG_PATH` = `secure_config.json`
- `PANEL_PASSWORD_HASH` = 第 2 步生成的哈希
- `JC_RSA_PUBLIC_KEY_PEM` = `public.pem` 内容（换行改成 `\n`）
- `JC_RSA_PRIVATE_KEY_PEM` = `private.pem` 内容（换行改成 `\n`）

添加完后，点 `Redeploy`（或触发一次新部署）。

### 第 6 步：在 GitHub 配 Actions Secrets（定时发信用）

GitHub 仓库 -> `Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`，添加：

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASS`
- `FROM_EMAIL`
- `SMTP_USE_SSL`（可选：`true/false`；若端口是 465 会自动视为 `true`）
- `SMTP_STARTTLS`（可选：`true/false`；非 SSL 时默认 `true`）
- `SMTP_TIMEOUT`（可选：连接超时秒数，默认 `20`）
- `SMTP_DEBUG`（可选：`true` 开启 SMTP 调试日志，排查连接/握手问题）
- `JC_TO`（多个邮箱用逗号分隔）
- `JC_RSA_PRIVATE_KEY_PEM`（`private.pem` 内容，换行改成 `\n`）

> `JC_MEMBERS / JC_TEMPLATE / JC_START_WED / JC_SUBJECT` 不放 Secrets，全部由前端加密写入 `secure_config.json`。

### 第 7 步：打开前端页面，初始化业务配置

1. 打开 Vercel 分配给你的域名首页
2. 在“访问密码”输入你的管理密码
3. 点“登录（会话 30 分钟）”
4. 填写：
   - 成员名单（每行一个）
   - 邮件主干模板
   - 轮换锚点周三（日期）
   - 邮件主题模板（可选）
5. 点“加密保存成员与模板”
6. 再点“刷新状态”，确认页面能正常读到状态

### 第 8 步：手动测试一次 GitHub Actions

1. GitHub 仓库 -> `Actions`
2. 找到 `Journal Club mail (JST Tue 12:00)`
3. 点 `Run workflow`
4. 观察日志是否成功，是否有 SMTP 报错

### 第 9 步：验证成功标准（按这个对照）

- 前端能登录，不报 401
- 前端能保存加密配置，不报 500
- 仓库里 `secure_config.json` 是密文（不是明文名单）
- Actions 手动运行成功
- 收件人能收到邮件（或 SMTP 日志显示成功）

### 第 10 步：上线后每周维护动作（1 分钟）

- 周二看一眼 Actions 是否成功
- 需要暂停时，在前端设置跳过周数
- 人员/模板调整时，在前端修改并加密保存

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
- **轮值节奏**：轮值按“轮换一次-保持一次”交替循环（每次**实际发送成功**才推进一次节奏）。
- **发表日期（邮件中的 {{month}}/{{day}}）**：
  - 轮换周：当周周三（自动发送日次日）
  - 保持周：下一周周三（自动发送日第 8 天）
- **对应周三日期**：由“轮换锚点周三”起每 7 天递增，用于计算当周/下一周的周三。
- **锚点与成员**：
  - 成员名单在保存时按字母序规范化，并视为**首尾相连的固定环**，顺序不因轮值而改变；
  - **轮换锚点周三**（JST）+ **环上起点（锚点讲者）**：起点为该人在字母序环中的位置，之后仅按序号沿环依次选下一位；
  - 保存加密配置后，会清除 `state.json` 中的 `bootstrapped_v1` / `anchor_sig`，下次发送前按新锚点重新对齐。
- **轮值推进**：
  - **轮换周**与**保持周**仍交替；同一轮值人在保持周重复一次；
  - **保持周**发送成功后，`ring_index` 沿环 +1（不移动名单中的顺序）。
- **跳过周数**：
  - `skip_weeks_remaining > 0` 时本周不发送并减 1；
  - 跳过时不会推进“轮换/保持”节奏，也不会改变成员队列顺序；
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

## 5. 环境变量配置（已按当前实现更新）

建议分别在 **Vercel Project Env**（管理 API 用）和 **GitHub Actions Secrets**（定时发送用）中配置。

### 5.1 GitHub Actions Secrets（发送任务运行时必须）

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASS`
- `FROM_EMAIL`
- `JC_TO`（收件人邮箱组，逗号分隔）
- `JC_RSA_PRIVATE_KEY_PEM`（解密 `secure_config.json` 所需私钥）

### 5.2 Vercel Environment Variables（前端管理/API 必须）

- `GITHUB_TOKEN`（可读写当前仓库）
- `GITHUB_REPO`（格式：`owner/repo`）
- `GITHUB_BRANCH`（可选，默认 `main`）
- `STATE_PATH`（可选，默认 `state.json`）
- `SECURE_CONFIG_PATH`（可选，默认 `secure_config.json`）

### 5.3 安全相关（Vercel 端）

- `PANEL_PASSWORD_HASH`（管理密码的 SHA-256 hex）
- `JC_RSA_PUBLIC_KEY_PEM`（公钥，`\n` 形式换行）
- `JC_RSA_PRIVATE_KEY_PEM`（私钥，`\n` 形式换行）

### 5.4 不再使用的明文变量（不要再配置）

- `JC_MEMBERS`
- `JC_TEMPLATE`
- `JC_START_WED`
- `JC_SUBJECT`

这些内容已经迁移到加密配置，由前端写入 `secure_config.json`。

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
   - 成员名单（每行一人，保存前会自动按字母序排序）
   - 邮件主干模板
   - 轮换锚点周三（JST）+ **环上起点（锚点讲者）**（下拉框与名单同步）
   - 可选主题模板
4. 点击“加密保存成员与模板”（保存后会重置 `state.json` 的 bootstrap 标记，以便按新锚点重新对齐）。

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