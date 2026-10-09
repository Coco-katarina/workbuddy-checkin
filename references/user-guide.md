# WorkBuddy签到助手

> **版本**：3.1.7

把 WorkBuddy「Buddy 加油站」每日签到、**派猫猫旅行**与**多渠道消息推送**做成**自动化**（WorkBuddy签到助手）：本机登录态可用时直接调用官方接口完成领取与派遣（**零配置**）；本机登录态无法在本地解密时（典型为 macOS 标准安装），改用**一次网页授权**取得的会话凭据，同样能自动签到。**无需点击 GUI、无需 OCR、无需第三方依赖**。

- **默认一次跑完两件事**：每日签到 + 派猫猫旅行全自动闭环（先领旅行积分，再派 Buddy 出门）。
- 执行完弹出**操作系统级桌面通知**（跨平台：Windows / macOS / Linux），展示签到结果、积分余额与旅行状态；无桌面会话时自动跳过，不影响签到。
- **12 类渠道消息推送**（可选）：钉钉 / 飞书 / 企业微信 / 微信 / 邮件 / 短信 / QQ / Slack / Telegram / Bark / 通用 Webhook / 系统通知，接口与配置项对标「消息推送」技能。
- 提供 **环境自检**（`--diagnose`）与 **配置模板生成**（`--init-config`），降低首次配置门槛。

> 适用对象：希望「每天自动领 Buddy 加油站积分」的 WorkBuddy 用户。安装 WorkBuddy签到助手 后，由 WorkBuddy 自带自动化在每天 09:00 触发；脚本零第三方依赖，仅用 Python 标准库。

## 它能做什么

- 查询今日是否已签到（`--check-only` 只读）。
- 未签到时自动领取（默认 +100 积分，连续签到有额外奖励）。
- 今日已签则幂等跳过，不重复领取。
- 签到后展示**当前积分余额**（若接口返回 balance / total_credits 等字段，结果中含 `balance`）。
- **派猫猫旅行（默认随签到一起跑）**：
  - 查状态：空闲 / 旅行中（含到达倒计时）/ 已到达待领取。
  - **领旅行积分**：Buddy 到达后自动领取。
  - **派 Buddy 出门**：空闲且未达每日上限时自动派出（地点随机，或 `--location N` 指定）。
  - 已达每日上限会自动跳过，**不会浪费次数也不会报错**。
- **桌面通知（默认开启）**：每次执行后弹出系统级 toast，展示结果与余额；加 `--no-notify` 可关闭。
- **可选多渠道消息推送**（钉钉 / 飞书 / 企业微信 / 微信 / 邮件 / 短信 / QQ / Slack / Telegram / Bark / 通用 Webhook / 系统通知）：
  - 签到**失败**时推送（断网 / 未登录 / 登录态失效）——默认开启。
  - 签到**成功**时推送播报——默认关闭，配置 `success_notify: true` 开启。
  - 单渠道失败互不影响；付费渠道（短信）需 `--confirm-paid` 显式放行。

## 快速开始

1. 安装本 Skill 后，让 WorkBuddy 执行「帮我设置 WorkBuddy 每日自动签到」。
2. Skill 会先跑一次**只读探测**判断走哪条通道：
   - **本机登录态可用**（典型 Windows）→ 直接就能用，**无需任何配置**。
   - **本机登录态无法在本地解密**（典型 macOS）→ 引导你做一次网页授权（约 30 秒，见下一节）。
   - **已保存过长效令牌** → 走通道 C，客户端不开也能自动签到（见下下节）。
3. Skill 把脚本落位到 `~/.workbuddy/scripts/`（`auto_checkin.py` / `workbuddy_checkin.py` / `web_auth.py` / `rt_auth.py` / `push_message.py` 五个文件，必须同目录）并创建每天 09:00 的自动化。
4. 次日 09:00 自动领取；之后在 Buddy 加油站核对积分 +100。

手动运行（调试用）：

```
python "%USERPROFILE%/.workbuddy/scripts/auto_checkin.py"                     # 推荐：自动择通道
python "%USERPROFILE%/.workbuddy/scripts/auto_checkin.py" --probe             # 只读：报告该走哪条通道
python "%USERPROFILE%/.workbuddy/scripts/auto_checkin.py" --check --no-notify # 仅查询，不领取
python "%USERPROFILE%/.workbuddy/scripts/push_message.py" --title 测试 --content 你好 --ready  # 只看已就绪渠道
```

> **Python 环境**：本技能在 WorkBuddy 代理内运行，代理自带 Python，**用户无需额外安装**；若要在 WorkBuddy 之外独立使用系统分享包，安装脚本会自动检测 Python 并提示安装（或直接使用 WorkBuddy 托管 Python `~/.workbuddy/binaries/python`）。

## 本机签不了？做一次网页授权（通道 B，授权一次长期有效）

先跑一次只读探测，看该走哪条通道：

```
python "%USERPROFILE%/.workbuddy/scripts/auto_checkin.py" --probe
```

读 `recommendation` 字段即可：

| `recommendation` | 含义 | 你要做什么 |
|---|---|---|
| `channel_local` | 本机登录态可用（典型 Windows） | **什么都不用做**，直接就能自动签到 |
| `channel_web` | 网页授权已就绪 | 什么都不用做 |
| `need_setup` | 本机登录态无法在本地解密（典型 macOS） | 按下面做一次网页授权 |

> 为什么 macOS 必须授权？macOS 标准安装下 WorkBuddy 客户端受系统完整性保护，第三方程序无法在本地
> 取得解密所需的密钥，系统凭据库中也没有对应条目——**本机没有任何可用的本地途径**。
> 所以可行且合规的做法，是把你**自己在网页端已经登录的会话**授权一次给脚本。

### 授权步骤（约 30 秒，只需一次）

1. 浏览器打开并登录 `https://www.workbuddy.cn`（**与客户端同一账号**）。
2. 让 WorkBuddy 打印授权码工具，或在终端运行：
   ```
   python "%USERPROFILE%/.workbuddy/scripts/web_auth.py" --snippet
   ```
3. 按 `F12` 打开开发者工具 → 切到 **Console** → 把工具里「方式A」那一行整段粘贴回车。
   授权码会自动复制到剪贴板（也可以把「方式B」存成浏览器书签，之后在已登录页面点一下即可）。
4. 回到对话，把授权码贴给 WorkBuddy；它会执行
   `python web_auth.py --setup-code '<授权码>'` 完成保存与校验。

授权码自带**原始 User-Agent**，因此不受官方网关「会话与 UA 绑定」的影响，一次粘贴即可生效。
授权成功后再跑 `auto_checkin.py` 就能自动签到与派猫猫了。

### 如果授权码方式读不到会话

少数情况下页面的会话 Cookie 标记为 HttpOnly，页面脚本读不到它，此时改用另外两种方式（任一即可）：

| 方式 | 怎么做 | 命令 |
|---|---|---|
| **Copy as cURL** | 在开发者工具 Network 里找到任意接口请求 → 右键 → Copy → Copy as cURL，整段复制 | `python web_auth.py --setup-curl '<粘贴的 cURL>'` |
| **导出 HAR** | Network → 右键 → 「保存所有为 HAR」，存成文件 | `python web_auth.py --setup-har <文件路径>` |

你也可以把上面任一段内容先存成文件再交给脚本（避免进入命令历史）：

```
python "%USERPROFILE%/.workbuddy/scripts/web_auth.py" --setup-file <文件路径>
```

### 凭据放在哪、怎么撤销

- 只保存在本机 `~/.workbuddy/checkin-cred.json`（POSIX 下权限 `600`），脚本只读这一个文件。
- 凭据随网页会话过期而失效；届时脚本会返回 `auth_failed`，**重新授权一次**即可。
- 想清除：`python web_auth.py --forget`，或直接删掉该文件。
- 结果里只出现脱敏标识（如 `cookie(312 字符)`），**不会回显凭据明文**。

## 不想依赖客户端？保存一次长效令牌（通道 C，v3.1.7 新增）

适合两种场景：**电脑上客户端不常开**，或想让定时签到**完全不依赖客户端是否在运行**。

### 配置步骤（一次即可，约 10 秒）

1. 在**客户端已登录**的电脑上，运行：
   `python rt_auth.py --export-rt --save`
   脚本自动读取你本机的登录态，把长效令牌保存到本机（无需手动查看任何文件）。
2. 之后运行 `python auto_checkin.py` 或 `python rt_auth.py`，即自动完成签到与派猫猫。
3. 每次运行时脚本会自动用长效令牌换取新的接口令牌，并把轮换出的新令牌写回本机，**长期自续**。

> 如果你有在其他设备/工具上保管的令牌，也可以手动录入：`rt_auth.py --setup-rt '<令牌>'` 或 `--setup-rt-file <文件>`。

### 令牌放在哪、怎么撤销

- 只保存在本机 `~/.workbuddy/checkin-rt.json`（POSIX 下权限 `600`），与网页授权的凭据文件相互独立。
- 查看保存状态（脱敏）：`python rt_auth.py --info`。
- 想清除：`python rt_auth.py --forget`（置空覆盖）。
- 令牌**等同账号钥匙**，请勿分享给任何人；如需作废，重新登录客户端即可。
- 若长期未用导致服务端会话过期，脚本会明确提示「刷新失败」，重新执行一次 `--export-rt --save` 即可恢复。

### 信息查询

- `python rt_auth.py --check` —— 只读查询：今日签到状态 / 连签天数 / 积分余额 / 派猫猫旅行状态。


## 「去试试」与即时签到

> 重要：**安装本 Skill 不等于已经签到**，也不会自动建任务。需要你说一句话触发。

- **只签一次（推荐先试这个）**：对 WorkBuddy 说「帮我签到一次」或「现在签个到」→ Skill 直接运行脚本完成本次领取，不创建任何定时任务。适合点「去试试」后立即验证是否可用。
- **设置每日自动签到**：说「帮我设置 WorkBuddy 每日自动签到」→ Skill 把脚本落位到 `~/.workbuddy/scripts/` 并创建每天 09:00 的 recurring 自动化，之后每天自动领。
- 若当天已手动签过，脚本会返回 `skip_already_signed`（幂等跳过），这是正常的，不是失败；返回结果会一并给出当前积分余额（若接口提供）。

## 派猫猫旅行（默认随签到一起执行）

让 Buddy 出门旅行，回来领积分。脚本按「**先领后派**」的闭环自动处理：

1. 查状态 → 若 `arrived`（已到达）先领积分，再重新查状态。
2. 此时若 `idle`（空闲）且**未达每日上限** → 派出 Buddy。
3. 若 `traveling`（旅行中）→ 不派遣，只展示还需多久到达。

| 状态 | 含义 | 脚本做什么 |
|---|---|---|
| 空闲 `idle` | 可派遣 | 未达上限则派出 |
| 旅行中 `traveling` | 在路上 | 不派遣，展示到达倒计时 |
| 已到达 `arrived` | 可领积分 | 自动领取 |

**四个地点收益完全一样**（随机 1-4 小时、5-10 积分）：咖啡馆 / 商场店铺 / 健身房 / 古镇客栈——所以随机即可，不用纠结选哪个。

专用命令：

```
python workbuddy_checkin.py travel                 # 只查旅行状态（只读，不签到）
python workbuddy_checkin.py travel --travel-auto   # 只跑旅行闭环（不签到）
python workbuddy_checkin.py --no-travel            # 只签到，跳过旅行（最快）
python workbuddy_checkin.py --location 3           # 指定派去健身房（1-4）
```

安全设计：派出前必查每日上限，达上限**一个写请求都不发**；已到达不会丢积分，下次运行自动补领；旅行接口偶尔不可用时静默跳过，**不影响签到结果**。

## 桌面通知（默认开启）

- 每次执行后都会弹系统通知，展示「签到结果 + 积分余额（若有）」。
- 无需任何配置，开箱即用；受 `--no-notify` 参数一并关闭。
- 跨平台：Windows 气球提示 / macOS 通知中心 / Linux notify-send。
- 注意：无桌面会话（如锁屏、09:00 系统任务未登录、服务器无 GUI）时可能不弹，但 `stdout` 的 JSON 结果与 `checkin.log` 仍会完整记录，不影响签到。

## 配置消息推送（可选，12 渠道）

签到失败（默认）或成功（需开启）时，可推送到以下渠道。**配置完全可选**——不配任何渠道时，脚本仅输出 JSON 结果，行为与之前完全一致。

| 渠道 | 标识 | 必填配置项 | 备注 |
|---|---|---|---|
| 钉钉群机器人 | `dingtalk` | `webhook` | 可选 `secret` 加签 |
| 飞书群机器人 | `feishu` | `webhook` | 可选 `secret` |
| 企业微信群机器人 | `wecom` | `webhook` | |
| 微信（PushPlus 中转） | `wechat` | `pushplus_token` | 推送到个人微信 |
| 邮件 | `email` | `smtp_host` / `smtp_user` / `smtp_pass` / `from` / `to` | `smtp_pass` 填**授权码**非登录密码 |
| 短信 | `sms` | `url` / `payload_template` | **付费渠道**，需 `--confirm-paid` |
| QQ | `qq` | `url` | 需自备推送网关 |
| Slack | `slack` | `webhook_url` | |
| Telegram | `telegram` | `bot_token` / `chat_id` | |
| Bark（iOS） | `bark` | `server` / `device_key` | 也兼容整条 `bark_url` |
| 通用 Webhook | `webhook` | `url` | 可选 `payload_template` / `secret` |
| 系统通知 | `system` | **无需配置** | 写本地 `notifications.jsonl` + 桌面 toast |

### 写法一：新结构 `channels`（推荐，渠道最全）

新建 / 编辑 `%USERPROFILE%\.workbuddy\scripts\notify_config.json`：

```json
{
  "enabled": true,
  "success_notify": false,
  "channels": {
    "dingtalk": { "webhook": "https://oapi.dingtalk.com/robot/send?access_token=你的TOKEN" },
    "feishu":   { "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/你的HOOK" },
    "wecom":    { "webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的KEY" },
    "wechat":   { "pushplus_token": "你的PushPlus_token" },
    "email": {
      "smtp_host": "smtp.qq.com", "smtp_port": 465, "smtp_mode": "ssl",
      "smtp_user": "你的邮箱@qq.com", "smtp_pass": "邮箱授权码",
      "from": "你的邮箱@qq.com", "to": "接收邮箱@qq.com"
    },
    "bark":     { "server": "https://api.day.app", "device_key": "你的KEY" },
    "webhook":  { "url": "https://你的服务/hook", "payload_template": "{\"title\":\"{{title}}\",\"text\":\"{{content}}\"}" }
  }
}
```

### 写法二：旧结构扁平字段（向后兼容，仅微信三通道）

沿用 2.x 的老配置**无需改动**，脚本会自动归一化后与新结构合并：

```json
{
  "enabled": true,
  "success_notify": false,
  "wecom_webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的群机器人KEY",
  "pushplus_token": "你的PushPlus_token",
  "bark_url": "https://api.day.app/你的KEY/"
}
```

### 行为约定

- `enabled: false` 或配置文件不存在 → 不推送，仅输出 JSON 结果（失败时退出码仍为 1）。
- `success_notify: true` 时，签到成功也会推一条播报；默认 `false` 仅失败时提醒。
- **单渠道失败隔离**：某渠道报错只记录该渠道失败，不影响其他渠道与签到退出码。
- **付费闸门**：`sms` 未显式加 `--confirm-paid` 时一律跳过并标记，不会意外产生短信费用。
- 仅向**已配置就绪**的渠道发送；未配的渠道会标记为 `unconfigured`，不会报错。
- 推送结果记录在结果 `detail.notify` / `detail.notify_success`（含渠道名与状态，**不含任何密钥值**），**不影响签到退出码**。
- 不想手写？运行 `python workbuddy_checkin.py --init-config` 自动生成双结构 `notify_config.json.example` 模板。
- 密钥只用在本机，**不进脚本、不进本 Skill 目录**，可放心转发给他人。

### 只推指定渠道 / 单测推送

```
python workbuddy_checkin.py --push-channels dingtalk,email     # 本次仅推这两个渠道（覆盖配置）
python workbuddy_checkin.py --confirm-paid                     # 放行付费渠道（短信）
python push_message.py --title "测试" --content "hello" --channels system   # 独立测试某渠道
python push_message.py --ready                                 # 只列出已就绪渠道（只读，不发送）
```

## 环境自检（--diagnose）

安装后不确定环境是否就绪？运行：

```
python "%USERPROFILE%/.workbuddy/scripts/workbuddy_checkin.py" --diagnose
```

会输出一份 JSON 自检报告，逐项检查（**只读、不触发任何签到请求**）：

| 检查项 | 说明 |
|------|------|
| `python` | 当前 Python 版本是否满足 ≥3.6 |
| `auth` | 登录态文件是否存在、token 是否可用（含 5.6.2+ 信封识别）、是否已过期 |
| `network` | 能否解析签到域名 DNS（best-effort） |
| `desktop` | 当前是否有桌面会话（影响通知能否弹出） |
| `notify_config` | 推送配置是否存在、`ready`（已就绪渠道）/ `unconfigured`（缺配置渠道）各是哪些 |

对照报告即可快速定位「为什么签不了」：例如 `auth.found=false` 说明没登录，`network.dns_ok=false` 说明网络不通，`auth.expired=true` 说明要重新登录客户端。

### 离线算法自测（--self-test-atrest）【v3.1.0 新增】

`--self-test-atrest` 可**不联网、不读登录态、不依赖客户端**地验证 5.6.2 信封解密算法是否正确：用随机密钥构造 AES-256-GCM 信封 → 加密 → 解密往返，并交叉用 `cryptography` 与内置纯 Python 实现互验；同时验证「篡改密文 / authTag / AAD 均被 GCM 拒绝」。全部通过才说明解密实现可信赖。

```
python scripts/workbuddy_checkin.py --self-test-atrest
```

## 客户端 5.6.2+ 登录态加密（AtRestEncryption）兼容【v3.1.0 新增】

多名用户反馈「客户端被强行更新到 5.6.2 后，签到技能无法签到」。原因与解决办法：

- **根因**：5.6.2 起客户端默认强制开启 AtRestEncryption，把登录态里的 `auth.accessToken` 从明文 JWT 改成 AES-256-GCM 信封（`{"$wbEncrypted":1,"envelope":"..."}`）。旧脚本（v3.0.0 及更早）直接把 `accessToken` 当明文 JWT 鉴权，导致失败。
- **解决（本 v3.1.0）**：自动识别两种登录态——明文（旧版 / 加密关闭）直接当 JWT 用，完全向后兼容；信封（5.6.2+）则本地 AES-256-GCM 解密后再取出 JWT 签到。全程不打印真实 token。
- **解密密钥（atRestSecretKey）获取方式**：该密钥**不落盘**，由运行中的客户端在运行时提供（因此磁盘上没有任何可直接替代的文件）。脚本按以下顺序取得：
  1. 环境变量 `WORKBUDDY_ATREST_KEY` —— 直接给密钥串（推荐，尤其系统级定时任务 / 分享包）。
  2. 环境变量 `WORKBUDDY_ATREST_KEY_FILE` —— 指向含明文密钥，或由当前用户加密保存的密钥块的文件。
  3. 登录态目录下由客户端加密保存的密钥块，用当前用户身份解开，并按信封 `keyId` 校验匹配（无需硬编码路径）。
  4. **运行中的客户端提供的密钥（v3.1.1 起，Windows 主路径）**：当**客户端已启动并登录**、且脚本与客户端在同一用户下运行时，脚本可直接取得该密钥，并按 `keyId = SHA256(SHA256(密钥串))[:16]` 严格校验匹配。
- **解密失败自动回退（3.1.1 增强）**：若以上方式都拿不到密钥（如客户端未运行、权限不足），脚本**不会直接报错退出**，而是自动回退到 WorkBuddy 代理自带的**明文兜底登录态** `~/.workbuddy/auth/workbuddy-desktop.info`（由 WorkBuddy 代理维护，通常为明文且有效），仍可正常签到与派遣。`--diagnose` 会逐个列出候选登录态的可用性与是否加密信封。
- **macOS 上以上本地方式均不可用**：系统凭据库中也没有对应条目。此时**改用通道 B 的网页授权即可**，不需要在本机寻找密钥。
- **AES 后端**：优先 `cryptography`，缺失回退 PyCryptodome，再缺失用内置纯 Python AES-256-GCM（已交叉对拍 + GCM 完整性校验）。
- 信封格式、派生规则、AAD 构造等完整技术细节见 `references/api-spec.md` 的「AtRestEncryption 信封格式」一节。

## 常见问题 FAQ

**Q：我在 Mac 上提示「无法定位密钥 / 需要授权」，怎么办？**

A：这是 macOS 的预期行为，不是故障——macOS 标准安装下 WorkBuddy 客户端受系统保护，任何第三方程序都无法在本地读出解密密钥。**做一次网页授权即可**：在你已登录 `www.workbuddy.cn` 的浏览器里运行技能提供的授权码工具（约 30 秒），把生成的授权码贴回来，之后就能长期自动签到。

**Q：网页授权安全吗？授权码里是什么？**

A：授权码里是**你自己在网页端的登录会话凭据**，外加原始 User-Agent 与站点域名（用于通过官方网关的会话绑定）。它保存在本机 `~/.workbuddy/checkin-cred.json`（POSIX 下权限 600），只用于调用官方签到接口；脚本不会把它发给任何第三方，输出里也只显示脱敏标识。授权码小工具本身只把结果复制到**你自己的剪贴板**，不读取本机文件、不向任何服务器发送数据。想撤销时跑 `web_auth.py --forget` 或直接删除该文件，在网页端退出登录也会使其失效。

**Q：安装技能后没有 Python 能用吗？**
A：在 WorkBuddy 代理内运行，代理自带托管 Python，用户无需安装。若脱离代理独立使用，脚本会在 Python<3.6 时给出友好提示（不抛堆栈）；也可直接用 `~/.workbuddy/binaries/python`。

**Q：提示「未找到本机登录态文件」怎么办？**
A：说明 WorkBuddy 客户端未登录或登录态路径变更。请先打开 WorkBuddy 客户端登录一次，再运行脚本。

**Q：提示「登录态已过期」怎么办？**
A：客户端登录态失效（如长期未登录、token 过期）。重新打开 WorkBuddy 客户端登录即可刷新；无需重装技能。

**Q：签到成功但没弹桌面通知？**
A：常见于无桌面会话（锁屏、09:00 系统任务未登录、服务器无 GUI）。属预期行为，stdout 与 `checkin.log` 仍有完整记录。加 `--no-notify` 会主动关闭通知。

**Q：怎么关闭桌面通知 / 消息推送？**
A：桌面通知与消息推送均可加 `--no-notify` 一并关闭（调试用）。若要永久关闭推送，删除 `notify_config.json` 或将 `enabled` 设为 `false`；要关闭成功播报，将 `success_notify` 设为 `false`；只想临时少推几个渠道，用 `--push-channels` 指定。

**Q：定时任务到点没跑？**
A：依次检查：电脑是否开机、WorkBuddy 客户端是否退出、是否联网、09:00 前后是否保持客户端运行。系统级定时任务（计划任务/launchd/cron）还需检查其自身是否启用。

**Q：网页版 WorkBuddy 能用吗？**
A：不能。签到入口仅 PC 客户端专属，网页版无签到接口，脚本也无法在网页版运行。

**Q：`code=10001` / HTTP 400 是失败吗？**
A：不是。这是接口返回的「今天已签到」，属正常幂等跳过，不是错误。

**Q：派猫猫旅行每次都会自动派遣吗？**
A：默认会——不带参数运行时，签到后自动跑「先领后派」闭环。只想签到、不想碰旅行，加 `--no-travel`。

**Q：为什么提示「今日派遣次数已用完」？**
A：服务端对每天派遣次数有限额，属正常现象，次日自动恢复，不是故障。脚本会跳过派遣、不发任何写请求。

**Q：Buddy 还在旅行中，能提前召回吗？**
A：不能，官方没有召回接口，只能等它到达后自动领取积分。

**Q：四个旅行地点哪个收益高？**
A：完全一样（都是随机 1-4 小时、5-10 积分），咖啡馆 / 商场店铺 / 健身房 / 古镇客栈任选，随机即可。想固定可用 `--location N`（1-4）。

**Q：旅行状态查不到 / 一直是空？**
A：旅行接口偶发不可用时会静默跳过，不影响签到。可单独跑 `python workbuddy_checkin.py travel` 看 `travel.available` 是否为 `true`。

**Q：如何获取各渠道的密钥？**
A：企业微信群机器人 Webhook 在群设置→群机器人添加；钉钉 / 飞书同理在群机器人里拿 Webhook（钉钉加签需额外填 `secret`）；PushPlus token 在 pushplus.plus 注册后获取；Bark key 在 iOS Bark App 内获取；邮件用邮箱服务商的 SMTP 授权码（非登录密码）。详见上方「配置消息推送」。

**Q：推送结果里某渠道显示 `unconfigured` / `skipped` 是什么意思？**
A：`unconfigured` = 该渠道必填项没配齐，不是网络问题；`skipped` = 主动跳过（例如短信未加 `--confirm-paid`）。`failed` 才是真的发送失败，通常看该渠道 webhook 是否失效。

**Q：配了推送但一直没收到消息？**
A：先跑 `python workbuddy_checkin.py --diagnose`，看 `notify_config.ready` 里有没有你的渠道。`ready` 为空说明字段缺失或文件不在默认路径。其次注意：失败推送只在`签到失败`时发；成功播报需要 `success_notify: true`；`--check-only` 纯查询不推送。

**Q：推送会不会误发短信产生费用？**
A：不会。`sms` 是唯一付费渠道，未显式带 `--confirm-paid` 时一律跳过，并在结果里标记为 skipped。

**Q：能不能推送到飞书/钉钉以外的自建系统？**
A：可以，用通用 `webhook` 渠道，填你的 `url` 与可选 `payload_template`（`{{title}}` / `{{content}}` 占位符）。

**Q：换电脑 / 重装后怎么恢复？**
A：在新机器安装 Skill 并说「帮我设置 WorkBuddy 每日自动签到」即可重建；推送密钥需重新填入各自的 `notify_config.json`（不要从旧机拷贝含密钥文件）。

## 反模式（不要这样做）

- ❌ **不要用网页版运行签到**：仅 PC 客户端支持，网页版无入口。
- ❌ **不要手动把 `auth.domain` 改成 `copilot.tencent.com` 等域名**：脚本自动以本机登录态为准，错误域名会 404。
- ❌ **不要用签到域名去调旅行接口**：旅行接口在 `www.workbuddy.cn` 且路径不带 `/v2`，与签到不是一套地址。
- ❌ **不要在「今日派遣已用完」时强行重复派遣**：服务端每日限额，脚本已自动跳过，绕过没有意义。
- ❌ **不要调用兑换 / 抽奖等未验证接口**：写操作只允许签到、领旅行积分、派 Buddy 三个。
- ❌ **不要把 `notify_config.json`（含 webhook/token）提交到任何仓库或分享给他人**：密钥仅本机使用。
- ❌ **不要往 `sms` 等付费渠道盲发**：付费渠道必须显式 `--confirm-paid`，不要为了图省事把它写进默认渠道列表。
- ❌ **不要把推送做成阻塞项**：推送失败绝不能让签到判定失败或改变退出码（脚本已做隔离，改造时勿破坏）。
- ❌ **不要给推送模块引入第三方 SDK / PyPI 依赖**：保持纯标准库，避免用户侧装包失败。
- ❌ **不要只复制主脚本、漏掉 `push_message.py`**：两者必须同目录；漏了会导致推送自动降级（签到本身仍正常，但收不到消息）。
- ❌ **不要用 crontab / 系统计划任务替代 WorkBuddy 自带自动化**（在 Skill 场景内）：脱离客户端可能拿不到登录态；系统级定时任务请用独立的「WorkBuddy 自动签到分享包」。
- ❌ **不要伪造、猜测或回显 token / accessToken / refreshToken**：脚本已脱敏，代理也不要打印凭据。
- ❌ **不要无限重试**：脚本内部最多重试 1 次，足够应对瞬时网络抖动。

## 排错速查

| 现象 | 含义 / 处理 |
|------|------|
| `HTTP 400 / code=10001` | 当天已签，属正常 |
| `未找到本机登录态文件` | 请先在 WorkBuddy 客户端登录 |
| `登录态已过期` | 重新登录客户端刷新 token |
| `HTTP 404` | 域名错误，脚本会自动用本机 `auth.domain`，无需手动改 |
| 旅行接口 404 | 域名必须是 `www.workbuddy.cn`，且路径不带 `/v2` |
| 自动化没跑 | 检查开机 / 客户端退出 / 联网 |
| 桌面通知不弹 | 无桌面会话，属预期，不影响签到 |
| 推送没收到 | 跑 `--diagnose` 看 `notify_config.ready`；空则配置缺字段或路径不对 |
| 渠道显示 `unconfigured` | 该渠道必填项缺失，非网络问题 |
| `sms` 显示 `skipped` | 付费渠道未加 `--confirm-paid`，属预期保护 |
| 推送失败但签到成功 | 属预期：推送与签到完全隔离，不影响退出码 |
| 5.6.2+ 信封解密失败 / 无法定位密钥 | 密钥不落盘、只驻留运行中客户端内存：Windows 上**先启动并登录客户端**再跑脚本（自动定位并按 keyId 校验），且脚本与客户端需同一用户；也可设置 `WORKBUDDY_ATREST_KEY` / `WORKBUDDY_ATREST_KEY_FILE`。**macOS 上属预期，改用网页授权（通道 B）** |
| 不知道该走哪条通道 | 跑 `auto_checkin.py --probe`，看 `recommendation`：`channel_local` / `channel_web` / `channel_rt` / `need_setup` |
| 网页授权后报 `auth_failed` | 网页会话过期，重新授权一次即可（授权码 / cURL / HAR 任一） |
| 网页授权报「校验未通过」 | 确认浏览器登录的账号与客户端一致；优先用授权码或 cURL（会带回原始 User-Agent）；仍失败则先重新登录网页端再取一次 |
| 授权码方式读不到会话 | 页面会话 Cookie 为 HttpOnly 且页面内无可用令牌，改用「Copy as cURL」或导出 HAR |
| 通道 C 报 `need_setup` | 尚未保存长效令牌：`rt_auth.py --export-rt --save` 一次即可 |
| 通道 C 报「长效令牌刷新失败」 | 令牌已失效（服务端会话过期）：重新 `--export-rt --save` 一次即可；`--info` 可先看保存状态 |

## 安全说明

- 脚本**只读**本机 WorkBuddy 登录态中的 `accessToken`，绝不修改、不删除、不外传。
- **网页授权（通道 B）的凭据只由你本人主动提供**：保存前会先向官方接口校验，通过才落盘；仅存本机 `~/.workbuddy/checkin-cred.json`（POSIX 600），随时可用 `web_auth.py --forget` 删除。授权码小工具只把结果写进你自己的剪贴板，不读取本机文件、不向任何服务器发送数据。
- **长效令牌（通道 C）同样只由你本人显式导出或录入**：仅存本机 `~/.workbuddy/checkin-rt.json`（POSIX 600），轮换出的新令牌只回写本机文件，不发送给任何第三方；随时可用 `rt_auth.py --forget` 清除。
- 任何输出都**不含真实 token 或推送凭据**（仅脱敏 `eyJhbG...xxxx`）；唯一的例外是你自己显式运行 `rt_auth.py --export-rt` 导出明文自行保管时。
- 只请求官方接口（签到以本机 `auth.domain` 为准、派猫猫旅行 `www.workbuddy.cn`、长效令牌刷新走官方插件网关）；仅当你主动配置了推送渠道，才会向你填写的那些地址发请求。
- 写操作只有四个：换取新接口令牌、签到、领取旅行积分、派出 Buddy；绝不触碰兑换 / 抽奖等其他接口。推送是本机独立外发行为，不会借推送去请求任意第三方地址（只访问你配置的渠道地址）。
- 推送模块零第三方依赖，纯 Python 标准库实现，不引入额外供应链风险。
- 短信等付费渠道默认被闸门拦住，避免误发产生费用。

## 隐私与共享

- 本 Skill 不含任何个人凭据，可放心分享给他人安装使用。
- 各机器互不影响：每人用各自的登录态与各自的推送密钥。

## 关闭 / 卸载

- **暂停每日自动签到**：在 WorkBuddy 的「自动化」列表里，把「WorkBuddy签到助手 · 每日自动签到」设为暂停（PAUSED）或删除即可，不影响脚本本身。
- **卸载本 Skill**：直接删除技能目录 `~/.workbuddy/skills/totorosir-workbuddy-checkin/`（Windows 即 `%USERPROFILE%\.workbuddy\skills\totorosir-workbuddy-checkin\`）。脚本副本 `~/.workbuddy/scripts/` 下的 `auto_checkin.py` / `workbuddy_checkin.py` / `web_auth.py` / `rt_auth.py` / `push_message.py` 可一并删除，不影响其他功能。若还配了推送，`notify_config.json` 可自行删除；**做过网页授权的还可删除 `~/.workbuddy/checkin-cred.json`（或先跑 `web_auth.py --forget`）；保存过长效令牌的还可删除 `~/.workbuddy/checkin-rt.json`（或先跑 `rt_auth.py --forget`）**。
- **彻底停止并清理**：暂停自动化 + 删除技能目录 + 删除脚本副本，即完全移除本能力，无残留系统服务。

## 关于作者

本技能由 TOTORO（totorosir）开发维护；相关更新、用法答疑与实战笔记发布于公众号 龙猫科技说。
