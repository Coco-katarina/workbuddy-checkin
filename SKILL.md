---
name: totorosir-workbuddy-checkin
display_name: WorkBuddy签到助手
display_name_en: WorkBuddy Check-in Assistant
description: WorkBuddy签到助手（WorkBuddy「Buddy 加油站」每日签到自动化 Skill，接口直签，跨平台 Windows/macOS/Linux）。当用户说"每天自动签到 / 每日签到 / 自动领 Buddy 加油站积分 / 现在签个到 / 帮我签到一次 / WorkBuddy 打卡 / 检查签到环境 / 派猫猫旅行 / 猫猫旅行 / 领旅行奖励 / 自动派猫猫 / Buddy 在旅行吗 / 推送签到结果 / 签到通知发钉钉 / 客户端更新后签不了 / 5.6.2 签到失败 / 登录态加密了 / Mac 上签不了 / 需要授权 / 长效令牌 / refreshToken / 不开客户端签到 / 令牌自动续期"时使用。三通道自动择一：本机登录态可用（如 Windows）零配置直签；无法本地解密（如 macOS）走一次性网页授权；或一次性保存长效令牌后自动续期，客户端不开也能签。支持派猫猫旅行全自动闭环（先领后派）、桌面通知与 12 类渠道推送（钉钉/飞书/企业微信/微信等），对齐 totorosir-push-message，可设每日 09:00 自动签到。
description_zh: WorkBuddy签到助手（WorkBuddy「Buddy 加油站」每日签到自动化 Skill，接口直签，跨平台 Windows/macOS/Linux）。当用户说"每天自动签到 / 每日签到 / 自动领 Buddy 加油站积分 / 现在签个到 / 帮我签到一次 / WorkBuddy 打卡 / 检查签到环境 / 派猫猫旅行 / 猫猫旅行 / 领旅行奖励 / 自动派猫猫 / Buddy 在旅行吗 / 推送签到结果 / 签到通知发钉钉 / 客户端更新后签不了 / 5.6.2 签到失败 / 登录态加密了 / Mac 上签不了 / 需要授权 / 长效令牌 / refreshToken / 不开客户端签到 / 令牌自动续期"时使用。三通道自动择一：本机登录态可用（如 Windows）零配置直签；无法本地解密（如 macOS）走一次性网页授权；或一次性保存长效令牌后自动续期，客户端不开也能签。支持派猫猫旅行全自动闭环（先领后派）、桌面通知与 12 类渠道推送（钉钉/飞书/企业微信/微信等），对齐 totorosir-push-message，可设每日 09:00 自动签到。
description_en: Auto check-in to WorkBuddy Buddy Station plus Buddy Travel, with three channels chosen automatically. With a usable local login state (typically Windows), a single request completes check-in with zero configuration. If the local login state cannot be decrypted (typically macOS), a one-time ~30s web-session authorization applies, whose code carries the original User-Agent, unaffected by gateway session binding. Or save a long-lived refresh token once; each run exchanges it for a fresh API token and writes the rotated token back for self-renewal, so check-in and Buddy dispatch keep working even with the desktop client closed. Cross-platform, with desktop notification and 12-channel push (DingTalk/Feishu/WeCom/WeChat/Email/SMS/QQ/Slack/Telegram/Bark/Webhook/system) mirroring the totorosir-push-message skill; supports a daily 09:00 automation.
category: 自动化
version: 3.1.7
author: totorosir
agent_created: true
---

# WorkBuddy签到助手（每日自动签到 · 派猫猫旅行 · 多渠道消息推送 · 三通道授权）

WorkBuddy「Buddy 加油站」每日签到本质是一次带鉴权凭据的 HTTP 接口请求，**不需要**模拟点击左下角「个人信息 → Buddy 加油站 → 签到」这一套 GUI 流程（自动化代理也没有点击桌面 UI 的能力）。

本 Skill 自带脚本，仅用 Python 标准库，零第三方依赖。

> 面向用户的完整说明（快速开始 / 授权步骤 / 桌面通知 / 消息推送配置 / 环境自检 / FAQ / 反模式 / 排错）见 `@references/user-guide.md`。
> 接口规范、登录态格式、字段与错误码、推送模块接口见 `@references/api-spec.md`。
> 自动化提示词、命令示例与推送配置示例见 `@references/examples.md`。
> 本文件只保留 Skill 元数据与代理执行所需关键信息，避免内容重复维护。

## 三条通道与统一入口（先读这一节）

| 通道 | 脚本 | 凭据来源 | 适用场景 | 用户成本 |
|---|---|---|---|---|
| **A · 本地登录态** | `scripts/workbuddy_checkin.py` | 本机 WorkBuddy 登录态 | 本机能直接用登录态换取接口令牌（典型为 Windows） | **零配置**，一次对话完成；要求客户端运行 |
| **B · 网页会话授权** | `scripts/web_auth.py` | 用户本人一次性授权 | 本机登录态无法在本地解密（典型为 macOS 标准安装） | 一次性授权约 30 秒，之后长期自动 |
| **C · 长效令牌自续期**（v3.1.7 新增） | `scripts/rt_auth.py` | 用户一次性保存自己的长效令牌 | 客户端不常开 / 定时任务独立运行 / 想让签到完全不依赖客户端 | 一次性导出或录入令牌，之后全自动且**客户端不开也能签** |

**统一入口 `scripts/auto_checkin.py`（推荐直接用它）**：自动在三条通道间择一执行，用户无需判断走哪条。

- 不带参数 → 先探测通道 A；可用就直接跑完；不可用则看通道 B 是否已授权；再看通道 C 是否已保存令牌；都不可用则输出一段可直接转述给用户的配置指引（**不做任何修改**）。
- `--probe` 只报告三条通道可用性（只读，不签到）。
- `--snippet` 打印网页授权码工具与用法。
- `--web` / `--local` / `--rt` 强制指定通道。
- 其余参数（`--check-only` / `--no-travel` / `--no-notify` / `--location` / `--domain` 等）原样透传给对应通道脚本。

> 背景：客户端从 5.6.2 起对登录态开启静态加密，解密所需的密钥由客户端在运行时提供、不落盘。Windows 上脚本可以按下方「客户端 5.6.2+ 登录态加密兼容」一节直接取得该密钥，因而零配置可用；macOS 标准安装下客户端受系统完整性保护，本机不存在可用的本地途径——**此时由用户本人授权即可**：把用户自己在网页端已登录的会话凭据一次性交给脚本，由其直接调用接口完成签到。

## 关键事实（Windows / macOS / Linux）

- **登录态文件（明文 JSON）**：`%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info`（macOS 为 `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/`；Linux 为 `~/.config/CodeBuddyExtension/Data/Public/auth/`）
- 文件内 `auth.accessToken`、`auth.domain`（示例值 `www.codebuddy.cn`）。
- **接口域名**：以登录态里的 `auth.domain` 为准，切勿硬编码。注意：签到与旅行接口不要写 `copilot.tencent.com`（会 404），应以本机 `domain` 字段为准；长效令牌刷新走插件网关（见「通道 C」与 `references/api-spec.md`）。
- **状态查询（只读）**：`POST https://<domain>/v2/billing/meter/checkin-activity-status`
  返回 `{"code":0,"data":{"today_checked_in":true/false,"streak_days":N,"daily_credit":100,...}}`（data 中可能含 **`total_credits`**（复数）/ `balance` 等余额字段，脚本会自动提取并展示）
- **领取签到**：`POST https://<domain>/v2/billing/meter/daily-checkin`
  - 成功：HTTP 200，`code:0`，返回 `credit` / `streak_days`（领取 100 积分）。
  - 已签到：HTTP 400，`code:10001`，`msg:"今天已签到，请明天再来"` —— **幂等，不会重复发**。

### 派猫猫旅行（关键：域名不同、且无 `/v2` 前缀）

- **旅行接口域名**：`https://www.workbuddy.cn`。**与签到域名不是同一个**，路径也**不带 `/v2` 前缀**；用签到域名或误加 `/v2` 一律 404。
- **旅行状态（只读）**：`GET /activity/growth/buddy/travel/status`
  - `data.state`：`idle` 空闲 / `traveling` 旅行中 / `arrived` 已到达待领取。
  - 另有 `daily_limit_reached`（今日派遣是否达上限）、`reward_credit`、`location`、`arrive_at`、`server_now`。
- **领取旅行积分**：`POST /activity/growth/buddy/travel/claim`（body `{}`）—— 仅 `arrived` 时可领。
- **派出 Buddy**：`POST /activity/growth/buddy/travel/depart`（body `{"location_id": N}`）
  - 仅 `idle` 且**未达每日上限**时派遣；地点 1-4（咖啡馆 / 商场店铺 / 健身房 / 古镇客栈），四个地点收益完全相同（随机 1-4 小时、5-10 积分），缺省随机。
  - 若服务端返回「no active buddy」等提示，说明该账号尚未在客户端激活派猫猫旅行，属账号状态而非脚本故障。
- **不会丢积分**：`arrived` 状态会一直保留，下次运行自动补领。
- 接口仅需鉴权凭据，**无需** Turing Shield 设备指纹。

完整字段、路径与错误码对照见 `@references/api-spec.md`。

## 通道 B：网页会话授权（一次性）

用于通道 A 不可用的场景（典型 macOS）。凭据只有以下来源，**均由用户主动提供，脚本不自行查找或提取**。

### 授权方式（按推荐度排列，任一即可）

| 方式 | 优点 | 适用 |
|---|---|---|
| **① 网页授权码（推荐）** | **自带原始 User-Agent**，一次粘贴即可；不受网关会话绑定影响 | 页面内可取到会话凭据时的默认选择 |
| **② Copy as cURL** | 可覆盖 HttpOnly 会话 Cookie；无需存文件；同样带 UA | 页面脚本读不到会话凭据时 |
| **③ 导出 HAR** | 兼容性最强 | 前两种都不便操作时的兜底 |
| ④ 显式密钥 `--set-key` | 无需打开浏览器 | 用户本人已持有 44 字符密钥时（走通道 A） |

**授权码**由随技能提供的浏览器小工具生成：`templates/auth-snippet.js`。用户在**自己已登录**的页面上运行它，它会把该页面自身的会话凭据连同原始 User-Agent 与站点域名打包成一段 `WBAUTH1:` 开头的授权码**复制到剪贴板**；工具本身不读取本机任何文件、不向任何服务器发送数据。

### 命令

- `python scripts/web_auth.py --snippet` —— 打印授权码工具与完整用法
- `python scripts/web_auth.py --setup-code '<授权码>'` —— 保存授权码（`-` 表示从标准输入读取）
- `python scripts/web_auth.py --setup-curl '<cURL>'` —— 保存从开发者工具复制的 cURL
- `python scripts/web_auth.py --setup-har <文件>` —— 从用户导出的 HAR 提取
- `python scripts/web_auth.py --setup-file <文件>` —— 从文本文件读取授权码 / cURL（避免进入命令历史）
- `python scripts/web_auth.py --check` —— 只校验凭据与查询状态（只读）
- `python scripts/web_auth.py --forget` —— 清除本机保存的网页授权凭据

### 行为约定

- 保存前**必定先校验**：向状态接口发一次只读请求，校验不通过则不落盘并给出明确原因。
- 解析结果可能是 cookie 或 bearer 两种形态，脚本会**依次校验、取首个通过者**（cookie 优先，因为它对应网页端会话）。
- 凭据仅保存在本机 `~/.workbuddy/checkin-cred.json`（POSIX 下 `0600`），脚本只读这一个文件。
- 凭据随网页会话过期而失效；失效时脚本返回 `auth_failed` 并提示**重新授权一次**即可。
- 结果 JSON 中只出现脱敏标识（如 `cookie(312 字符)` / `bearer:eyJhbG...xxxx`），**绝不回显凭据明文**。

## 通道 C：长效令牌自续期（一次性配置，v3.1.7 新增）

核心能力：用户一次性提供**自己的长效令牌**（refresh token，即客户端登录后由官方签发、用于换取新接口令牌的凭据）后，脚本每次运行时用它换取新的接口令牌完成签到与派猫猫，再把轮换出的新长效令牌**回写本机凭据库**，实现长期自续。配置完成后**不再要求客户端处于运行状态**，定时任务可完全独立运行。

### 令牌的获取（用户显式发起，脚本只使用、不自行收集）

| 方式 | 命令 | 说明 |
|---|---|---|
| **① 本机导出（推荐）** | `python scripts/rt_auth.py --export-rt --save` | 客户端已登录时自动读取本机登录态并保存，无需手动查看任何文件 |
| ② 导出明文自行保管 | `python scripts/rt_auth.py --export-rt` | 明文输出到终端（等同账号钥匙，用户自行复制到其他设备/工具使用） |
| ③ 手动录入 | `python scripts/rt_auth.py --setup-rt '<令牌>'` 或 `--setup-rt-file <文件>` | 使用用户已保管的令牌字符串录入 |

### 命令

- `python scripts/rt_auth.py` —— 签到 + 派猫猫闭环（与通道 A 行为一致，含余额展示与推送）
- `python scripts/rt_auth.py --check` —— 只查信息（只读）：签到状态 / 连签天数 / 积分余额 / 旅行状态
- `python scripts/rt_auth.py --no-travel` —— 只签到
- `python scripts/rt_auth.py travel` / `travel --travel-auto` —— 只查 / 只跑旅行闭环
- `python scripts/rt_auth.py --info` —— 查看本机令牌保存状态（脱敏，不含明文）
- `python scripts/rt_auth.py --forget` —— 清除本机保存的令牌（置空覆盖，不删文件）
- 其余参数与通道 A 一致（`--location` / `--push-channels` / `--no-notify` / `--confirm-paid` 等）

### 行为约定

- 令牌仅保存在本机 `~/.workbuddy/checkin-rt.json`（POSIX 下 `0600`），与通道 B 的凭据文件相互独立。
- 每次运行先用长效令牌换新接口令牌，再执行签到与旅行；轮换出的新长效令牌自动回写，写失败不影响本次签到（旧令牌仍可用作兜底）。
- 任何输出（结果 JSON / 日志 / 推送）都**不含令牌明文**；仅 `--export-rt`（用户显式发起）输出明文供用户自行保管。
- 长效令牌由官方签发、长期有效；若服务端会话过期导致刷新失败，脚本会明确提示**重新导出或录入一次**即可恢复。
- 轮换不使旧令牌立即失效：本通道与客户端各自的续期互不干扰，也不需要客户端同时在线。

## 客户端 5.6.2+ 登录态加密（AtRestEncryption）兼容【通道 A】

> 背景：客户端从 5.6.2 起强制开启登录态静态加密，旧脚本读到的 `accessToken` 不再是明文 JWT，导致签到失败。

- **形态**：登录态文件里的 `auth.accessToken` 不再是明文 JWT，而是 AES-256-GCM 信封：
  ```json
  {"$wbEncrypted":1,"envelope":"<base64 信封>"}
  ```
  其中 `envelope`（suite=1）结构为 `{suite, keyId, nonce(base64), authTag(base64), ciphertext(base64)}`。
- **脚本的处理**：自动识别两种登录态，二选一——
  - **明文字符串**（5.5.x 及更早 / 加密未开启）→ 直接当 JWT 用，向后兼容。
  - **信封对象**（5.6.2+）→ 在本地解密取出 JWT 再签到。
  - 解密全在脚本内完成，全程不打印真实 token（仅脱敏 `eyJhbG...xxxx`）。
- **密钥从哪来**：解密所需的 `atRestSecretKey`（44 字符规范 base64）**不落盘**，由运行中的客户端在运行时提供（同一把密钥也用于包裹 `~/.workbuddy/keyblob` 里的主密钥，因此该文件不能作为密钥的替代来源）。脚本按以下顺序取得：
  1. 环境变量 `WORKBUDDY_ATREST_KEY` —— 直接给 44 字符密钥串（最省事，适合系统级定时任务 / 分享包）。
  2. 环境变量 `WORKBUDDY_ATREST_KEY_FILE` —— 指向一个文件，里面是明文 44 字符密钥，或由当前用户加密的密文块。
  3. 登录态所在 `Data` 目录下由客户端加密保存的密钥块，用当前用户身份解开并用信封里的 `keyId` 校验匹配（派生规则：`key = SHA256(atRestSecretKey 字符串)`, `keyId = SHA256(key).hex()[:16]`）。
  4. **运行中的客户端提供的密钥（v3.1.1 起，Windows 主路径）**：当**客户端已启动并登录**、且脚本与客户端在同一用户下运行时，脚本可直接取得该密钥，并用信封 `keyId` 按派生规则严格校验匹配。**仅 Windows 提供该途径**；客户端提权运行而脚本未提权时会被系统拒绝。
- **解密失败的处理**：若以上方式都拿不到密钥（如客户端未运行、权限不足），脚本**不会直接报错退出**，而是自动尝试下一个候选登录态（候选里含 WorkBuddy 代理自带的明文兜底登录态 `~/.workbuddy/auth/workbuddy-desktop.info`，存在且可用时才生效）；全部失败才汇总报错。此时**改用通道 B（网页会话授权）即可**，无需在本机寻找密钥。
- **AES 实现与后端**：主实现优先用 `cryptography`（AES-GCM）；缺失时回退 PyCryptodome；两者都缺失则用内置**纯 Python AES-256-GCM**（标准 S-Box + GHASH + CTR）。三层实现已交叉对拍，且 GCM 完整性校验严格（篡改密文 / authTag / AAD 均会被拒绝）。
- **自测**：`python scripts/workbuddy_checkin.py --self-test-atrest` 可**离网、不读登录态、不依赖客户端**地验证 AES-GCM 加解密往返与纯 Python / 主实现互验。

## 版本说明

| 版本 | 说明 |
|---|---|
| **3.1.7**（当前） | 新增**通道 C 长效令牌自续期**：用户一次性导出/录入自己的长效令牌后，脚本每次运行自动换取新接口令牌并回写轮换结果，长期自续；配置后签到与派遣不再要求客户端运行。`auto_checkin.py --probe` 升级为三通道探测，新增 `--rt` 强制通道。通道 A / B 行为与 3.1.6 完全一致。 |
| 3.1.6 | 新增**通道 B 网页会话授权**与**统一入口**，补齐 macOS 等「本机登录态无法本地解密」场景的自动签到能力；授权支持网页授权码 / cURL / HAR 三种来源，授权码自带原始 User-Agent 以适配网关的会话绑定。通道 A 行为与 3.1.2 完全一致。 |
| 3.1.2 | 修复 5.6.2+ 加密登录态下密钥可能取不到的问题，并改进密钥取得失败时的诊断提示。 |
| 3.1.1 | 新增 Windows 主路径下自动取得客户端提供的密钥；加密登录态解密失败时自动尝试下一个候选登录态；`--diagnose` 逐个枚举候选登录态。 |
| 3.1.0 | 新增 WorkBuddy 客户端 5.6.2+ AtRestEncryption（AES-256-GCM 信封）登录态兼容；新增 `--self-test-atrest` 离线自测。 |
| 3.0.0 | 签到 + 派猫猫旅行闭环 + 12 渠道消息推送 + 桌面通知。 |

版本号变更不影响调用方式与配置格式；升级只需替换技能目录（或重装技能包），登录态、`notify_config.json` 与已保存的网页授权凭据均无需改动。

## 自带脚本

五个脚本，均只用 Python 标准库，零第三方依赖（必须同目录）：

- `scripts/auto_checkin.py` —— **统一入口**，自动在三条通道间择一执行
- `scripts/workbuddy_checkin.py` —— 通道 A：本机登录态 + 签到 + 派猫猫旅行
- `scripts/web_auth.py` —— 通道 B：网页会话授权与签到 + 派猫猫旅行
- `scripts/rt_auth.py` —— 通道 C：长效令牌自续期 + 签到 + 派猫猫旅行
- `scripts/push_message.py` —— 多渠道消息推送模块（被上述脚本按同目录导入，也可独立运行）

模板：

- `templates/notify_config.json.example` —— 推送配置模板
- `templates/auth-snippet.js` —— 网页授权码工具（用户在自己浏览器中运行一次）

### 通道 A 核心逻辑

1. 在 `LOCALAPPDATA` / `APPDATA` / `~/Library/Application Support` / `~/.config`（及 `~/.workbuddy/auth` 兜底）定位 `workbuddy-desktop.info`，只读取出 `accessToken` 与 `domain`；依次尝试所有候选登录态，加密态解密失败会自动跳到下一个可用来源。
2. 调 `checkin-activity-status`：若 `data.today_checked_in==true` → 直接 `skip_already_signed` 退出（不发领取请求）。
3. 否则调 `daily-checkin` 领取；响应 `code==10001` 或含"已签到" → 视为已签安全跳过；HTTP 200 且 `code==0` → 领取成功。
4. 非 2xx 也解析响应体（避免把"已签到 400"误判为异常）。
5. **输出 JSON 结果，全程不打印任何真实 token**（仅脱敏 `eyJhbG...xxxx`）。退出码：成功 0 / 失败 1。

### 通道 B 核心逻辑

1. 读取本机 `~/.workbuddy/checkin-cred.json`；不存在则返回 `no_cred` 并给出授权指引。
2. 用凭据调 `checkin-activity-status`；401/403 → `auth_failed` 并提示重新授权。
3. 其余分支与通道 A 一致（幂等跳过 / 领取 / 旅行 / 推送），结论字段结构也一致。

### 通道 C 核心逻辑

1. 读取本机 `~/.workbuddy/checkin-rt.json`；不存在则返回 `need_setup` 并给出导出/录入指引。
2. 用长效令牌换取新的接口令牌；刷新失败 → 明确提示重新导出或录入一次。
3. 轮换出的新长效令牌自动回写本机凭据库（写失败不影响本次签到）。
4. 其余分支（幂等跳过 / 领取 / 旅行 / 推送 / 余额）与通道 A 完全一致（复用同一套实现）。

### 通道 A 支持参数

- （无参数）签到 + 派猫猫旅行全自动闭环（**默认**）
- `--no-travel` 只签到，跳过旅行（最快档）
- `--check-only` 仅查询状态（只读，不领取、不写旅行）
- `travel` 只查派猫猫旅行状态（只读，不签到）
- `travel --travel-auto` 只跑旅行闭环（不签到）
- `--location N` 指定派遣地点（1-4，缺省随机）
- `--push-channels dingtalk,email` 仅向指定渠道推送（覆盖配置里的渠道集合）
- `--confirm-paid` 允许发送付费渠道（短信）；缺省时付费渠道一律跳过
- `--no-notify` 跳过全部推送与桌面通知（调试用）
- `--diagnose` 环境自检（Python / 登录态 / 密钥来源 / 网络 / 桌面会话 / 推送配置，只读）
- `--self-test-atrest` 仅自测 5.6.2 信封解密算法（不联网、不读登录态、不依赖客户端）
- `--init-config` 生成 `notify_config.json.example` 模板
- `--version` / `--help`

### 通道 C 支持参数

- （无参数）签到 + 派猫猫旅行全自动闭环（**默认**，自动续期长效令牌）
- `--check` / `--check-only` / `--status` 仅查询信息（只读：签到状态 / 连签 / 余额 / 旅行状态）
- `--no-travel` 只签到，跳过旅行
- `travel` 只查派猫猫旅行状态（只读）；`travel --travel-auto` 只跑旅行闭环（不签到）
- `--location N` / `--push-channels` / `--confirm-paid` / `--no-notify` 与通道 A 一致
- `--export-rt [--save]` / `--setup-rt` / `--setup-rt-file` / `--info` / `--forget` —— 令牌获取与管理
- `--version` / `--help`

推送模块也可独立运行（便于单独调试渠道）：`python scripts/push_message.py --title "标题" --content "正文" --ready`

**派猫猫旅行闭环顺序（先领后派）**
1. 查状态：`arrived` → 领取积分 → 重新查状态。
2. 此时若为 `idle` 且 `daily_limit_reached` 为假 → 派出；已达上限 → 跳过并说明。
3. 若 `traveling` → 不派遣，仅展示到达倒计时。

能力要点：
- **桌面通知（默认开启）**：每次执行后弹系统级 toast 展示结果与余额；受 `--no-notify` 抑制；无桌面会话时自动跳过，不影响签到。
- **失败消息推送（可选）**：`status!=ok` 时读取本地 `~/.workbuddy/scripts/notify_config.json`（若存在），向已就绪渠道推送失败提醒；配置缺失或通道异常则静默跳过，单渠道失败不影响其他渠道。
- **成功消息播报（可选，默认关闭）**：`notify_config.json` 中 `success_notify: true` 时，签到成功也会推送一条播报；默认 `false` 保持静默无打扰。
- **积分余额展示**：从状态/领取响应中尽力提取「积分余额」（**`total_credits`**（复数）/ `balance` / `points_balance` 等），写入结果 `balance` 字段并展示；接口未返回则自动跳过。
- **派猫猫旅行（默认随签到执行）**：**派出前必查 `daily_limit_reached`，达上限一个写请求都不发**；已到达不会丢积分。结果写入 `travel` 字段并拼进主消息与桌面通知；旅行接口不可用时静默降级，绝不改变签到结论。
- **环境自检（--diagnose / --probe）**：只读自检并输出 JSON 报告，便于首次安装后确认环境就绪。

## 调用本 Skill 时的搭建流程（照做即可）

当用户要求搭建/修复自动签到，或换机/重装后重建时：

1. **判断走哪条通道**（只读）
   - 跑 `python scripts/auto_checkin.py --probe`，读 `recommendation` 字段：
     - `channel_local` → 走通道 A，**无需任何配置**，直接跳到第 3 步。
     - `channel_web` → 通道 B 已授权，直接跳到第 3 步。
     - `channel_rt` → 通道 C 已保存长效令牌，直接跳到第 3 步。
     - `need_setup` → 走第 2 步做一次性配置。
   - 若用户明确说"Mac 上签不了 / macOS"，直接按通道 B 准备。
   - 若用户希望"客户端不开也能签 / 定时任务独立跑"，按通道 C 准备（`--export-rt --save` 一次即可）。

2. **通道 B / C：引导用户完成一次性配置**（只在必要时执行）
   - 通道 C（客户端已登录的电脑上首选，最省事）：运行 `python scripts/rt_auth.py --export-rt --save`，`status=ok` 即配置完成；之后客户端不开也能签到。
   - 通道 B（如通道 C 不可用，典型 macOS）：运行 `python scripts/web_auth.py --snippet`，把输出的**授权码工具与三步操作**原样转述给用户（打开并登录 `www.workbuddy.cn` → F12 Console 粘贴运行 → 授权码已复制到剪贴板）。
   - 用户把授权码贴回来后，运行 `python scripts/web_auth.py --setup-code '<授权码>'`。
   - `status=ok` 即授权成功；若返回 `fail`，按 `msg` / `hint` 如实告知用户原因（凭据过期 / 未取到会话 / 需改用 cURL 或 HAR），**不要**伪造或猜测。
   - 若用户页面会话为 HttpOnly、授权码方式取不到，改引导「Copy as cURL」→ `--setup-curl '<cURL>'`，或导出 HAR → `--setup-har <文件>`。

3. **落位脚本到稳定路径**
   - 把本 Skill 目录里的**五个脚本一起**复制到 `~/.workbuddy/scripts/`（必须同目录：统一入口按同目录导入三条通道，各通道按同目录导入推送模块；缺文件时对应能力自动降级）。
   - 跑一次 `python scripts/auto_checkin.py --check --no-notify` 验证凭据与接口连通（只读，不领取）。
   - 可顺带跑一次 `--diagnose`（通道 A）或 `--probe`，把自检报告读给用户，确认环境就绪（含推送渠道就绪情况）。

4. **验证领取分支（可选但建议）**
   - 跑一次不带参数的完整脚本：若当天已签 → 返回 `skip_already_signed`；若未签 → 返回 `clicked` 并提示用户去 Buddy 加油站界面核对 +100。

5. **创建 WorkBuddy 自带自动化**（不要用 crontab / launchd / 第三方定时器）
   - 用 `automation_update`（mode=create）创建 recurring 自动化：
     - name：`WorkBuddy签到助手 · 每日自动签到`
     - rrule：`FREQ=DAILY;BYHOUR=9;BYMINUTE=0`
     - status：`ACTIVE`
   - 自动化提示词（让代理跑统一入口并脱敏汇报，两条通道通用）见 `@references/examples.md`。

6. **向用户汇报**：走的哪条通道、自动化名称与执行时间、脚本路径；并提醒"若当天已手动签到会自动跳过；首个真实自动领取通常在次日 09:00，请在 Buddy 加油站核对积分 +100"。

## 消息推送（可选，12 渠道）

签到失败 / 成功时（按配置），脚本通过 `scripts/push_message.py` 向已就绪渠道推送提醒。**推送凭据只存在于本地 `notify_config.json`，永不进入脚本或技能目录**。

| 渠道 | 标识 | 必填配置项 |
|---|---|---|
| 钉钉群机器人 | `dingtalk` | `webhook`（可选 `secret` 加签） |
| 飞书群机器人 | `feishu` | `webhook`（可选 `secret`） |
| 企业微信群机器人 | `wecom` | `webhook` |
| 微信（PushPlus 中转） | `wechat` | `pushplus_token` |
| 邮件 | `email` | `smtp_host` / `smtp_user` / `smtp_pass` / `from` / `to` |
| 短信（**付费**） | `sms` | `url` / `payload_template` |
| QQ | `qq` | `url` |
| Slack | `slack` | `webhook_url` |
| Telegram | `telegram` | `bot_token` / `chat_id` |
| Bark（iOS） | `bark` | `server` / `device_key`（也兼容整条 `bark_url`） |
| 通用 Webhook | `webhook` | `url`（可选 `payload_template` / `secret`） |
| 系统通知 | `system` | **零配置**，写本地 `notifications.jsonl` + 桌面 toast |

**配置两种写法可混用**（`normalize_channels` 会归一化后合并）：
- 新结构 `channels` 映射 —— 渠道最全，推荐：`{"channels": {"dingtalk": {"webhook": "..."}, "email": {...}}}`
- 旧结构扁平字段 —— 微信三通道向后兼容：`wecom_webhook` / `pushplus_token` / `bark_url`

**行为约定**：
- `enabled: false` 或配置文件不存在 → 不推送，仅输出 JSON 结果（失败时退出码仍为 1）。
- 失败推送：仅在 `status!=ok` 时推送。
- 成功播报：仅当 `success_notify: true` 时推送；纯查询（`--check` / `--check-only`）不会推送。
- **单渠道失败隔离**：某渠道报错只记录该渠道失败，不影响其他渠道与签到退出码。
- **付费闸门**：`sms` 属付费渠道，未显式带 `--confirm-paid` 时一律跳过并标记，不会意外产生短信费用。
- 推送结果仅记录到 `detail.notify` / `detail.notify_success`（含渠道名与状态，**不含任何密钥值**），**不影响签到退出码**。

配置模板与真实示例见 `@references/examples.md`，模块接口规范见 `@references/api-spec.md`。

## 安全约束（务必遵守）

- 通道 A 只读登录态文件，绝不修改、绝不删除；令牌明文不外传，仅当用户显式运行 `rt_auth.py --export-rt` 时输出给用户本人保管。
- 通道 B 的凭据只由用户主动提供；保存前必先校验，仅存本机 `~/.workbuddy/checkin-cred.json`（POSIX 0600）。
- 通道 C 的长效令牌只由用户显式导出或录入；仅存本机 `~/.workbuddy/checkin-rt.json`（POSIX 0600）；轮换出的新令牌回写本机，不发送给任何第三方。
- 任何输出（终端、日志、汇报）都不得包含真实凭据；脚本已脱敏，代理也不要回显凭据。用户贴回的授权码 / cURL 在完成授权后不需要保留，也不要再回显。
- 推送凭据只存本地 `notify_config.json`；**不要**把填了真实密钥的配置文件放进技能目录、打包进 zip 或提交到仓库。
- 写操作仅限已验证的 4 个端点：长效令牌刷新（换取新接口令牌）、`daily-checkin`（签到）、`travel/claim`（领旅行积分）、`travel/depart`（派遣）。推送是独立的本地外发行为，不得借推送接口做任意地址请求。
- 只访问 WorkBuddy 官方域（`workbuddy.cn` / `codebuddy.cn` 及官方插件网关）；不要为推送额外引入第三方 SDK / PyPI 依赖；脚本只用 Python 标准库。
- 不安装 Electron；本 Skill 自身只用 WorkBuddy 自带自动化完成每日签到。
- 如需**系统级定时任务**（Windows 计划任务 / macOS launchd / Linux crontab，脱离 WorkBuddy 也能跑），请使用独立的「WorkBuddy 自动签到分享包」，与本 Skill 互不冲突、可并存。
- 不要在网页版尝试签到（网页版无签到入口，仅 PC 客户端专属）。通道 B 只是复用网页端**已登录的会话**调用同一组官方接口，并不代表网页版提供签到入口。

## 排错要点（详见 `@references/user-guide.md` 排错速查）

- `code=10001` 是今日已签，非错误。
- 404 一定是用了错误域名（脚本自动用本机 `auth.domain`）。
- **旅行接口 404 先查两件事**：域名必须是 `www.workbuddy.cn`（不是登录态里的 `auth.domain`），且路径**不能带 `/v2`**。
- 「今日派遣次数已用完」是正常的服务端每日限额，次日自动恢复，不是故障。
- 旅行中无法提前召回：官方没有召回接口，只能等到达后自动领取。
- **不知道该走哪条通道**：跑 `auto_checkin.py --probe`，看 `recommendation`。
- **通道 A 报「无法定位密钥」（macOS 常见）**：属预期，改用通道 B（`--snippet` → `--setup-code`）即可，不必在本机寻找密钥。进阶替代：本仓库 `tools/decrypt-token.js` 可经 CDP 短暂重启客户端解密登录态、导出 refreshToken 后走通道 C（`rt_auth.py --setup-rt-file`），详见 README。
- **通道 B 报 `auth_failed`**：网页会话过期，重新授权一次（授权码 / cURL / HAR 任一）；不必修改任何配置。
- **通道 B 报「校验未通过」**：确认浏览器登录的账号与客户端一致；优先用授权码或 cURL（会带回原始 User-Agent）；仍失败则先重新登录网页端再取一次凭据。
- **通道 C 报「长效令牌刷新失败」**：令牌已失效（服务端会话过期），重新运行 `rt_auth.py --export-rt --save` 导出一次即可恢复；`--info` 可先查看本机保存状态。
- **通道 C 报 `need_setup`**：尚未保存长效令牌，按「通道 C」一节的获取方式配置一次。
- **授权码方式取不到会话**：说明页面会话 Cookie 为 HttpOnly 且页面内无可用令牌，改用 `--setup-curl` 或 `--setup-har`。
- **推送没发出**：先跑 `--diagnose` 看 `notify_config.ready` 是否列出你的渠道；`ready` 为空说明配置缺字段或文件路径不对（默认 `~/.workbuddy/scripts/notify_config.json`）。
- **推送结果里某渠道 `unconfigured`**：该渠道必填项缺失，不是网络问题。
- **`sms` 显示 `skipped`**：付费渠道未加 `--confirm-paid`，属预期保护。
- 自动化没跑先查开机 / 客户端退出 / 联网。
- 桌面通知不弹通常是无桌面会话（锁屏/无 GUI），属预期，stdout 与 `checkin.log` 仍有完整记录。

## 关于作者

本技能由 TOTORO（totorosir）开发维护；相关更新、用法答疑与实战笔记发布于公众号 龙猫科技说。
