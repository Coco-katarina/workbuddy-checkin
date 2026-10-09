# 接口与登录态规范（WorkBuddy签到助手）

本文件供 Skill 执行时参考，包含登录态文件格式、签到接口、旅行接口、字段含义、错误码、余额字段候选名，以及推送模块的接口契约。
所有接口均通过本机已登录 WorkBuddy 客户端的 `accessToken` 鉴权，**只读**登录态，绝不修改。

---

## 1. 登录态文件

由 WorkBuddy PC 客户端登录后生成，明文 JSON。

### 路径（按操作系统自动探测）

| 系统 | 路径 |
|------|------|
| Windows | `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info`<br>（旧版可能在 `%APPDATA%` 同路径下） |
| macOS | `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info` |
| Linux | `~/.config/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info` |
| 兜底 | `~/.workbuddy/auth/workbuddy-desktop.info` |

脚本探测顺序：Windows 取 `LOCALAPPDATA` / `APPDATA` → macOS 取 `~/Library/Application Support` → Linux 取 `~/.config` → 最后兜底 `~/.workbuddy/auth`。

### 关键字段

```json
{
  "auth": {
    "accessToken": "<JWT>",
    "tokenType": "Bearer",
    "domain": "www.codebuddy.cn",
    "expiresAt": 1735689600000
  }
}
```

- `auth.accessToken`：鉴权令牌（JWT）。**任何输出都不得打印真实值**，脚本仅脱敏为 `eyJhbG...xxxx`。
- `auth.domain`：**接口域名以此为准**。示例值为 `www.codebuddy.cn`。网上文章写 `copilot.tencent.com` 会 404，切勿硬编码。
- `auth.expiresAt`：过期时间（epoch 毫秒/秒）。脚本兼容 13 位毫秒与 10 位秒；为空则跳过过期检查。

---

### 1.1 AtRestEncryption 信封格式（v3.1.0 新增）

客户端 **5.6.2+** 默认强制开启 AtRestEncryption（`buildMode` 由 `disabled` 改为 `required`）。此时 `auth.accessToken` 不再是明文 JWT，而是 AES-256-GCM 信封：

```json
{
  "auth": {
    "accessToken": { "$wbEncrypted": 1, "envelope": "<base64 信封>" }
  }
}
```

明文登录态（`accessToken` 为字符串）仍被完全兼容，行为同 v3.0.0。

#### 信封结构（suite=1）

`envelope`（base64 解码后）是 JSON 对象：

```json
{
  "suite": 1,
  "keyId": "<16 位小写 hex>",
  "nonce": "<base64, 12 字节>",
  "authTag": "<base64, 16 字节>",
  "ciphertext": "<base64>"
}
```

- 包装层 `{"$wbEncrypted":1, "envelope":"..."}` 与内层 `suite=1` 信封是两个层级：外层是 `ProtectedFieldCodec` 的字段封装，内层是 `AtRestCrypto` 的加密信封。
- `keyId` 由密钥派生（见下），用于确认解密所用密钥正确。

#### 密钥派生（byte-accurate，复刻 `normalizeAtRestKeyPayload`）

```
atRestSecretKey = 44 字符规范 base64 字符串（解码得 32 字节种子）
key   = SHA256( atRestSecretKey 的 UTF-8 字符串 )[:32]      # 32 字节 AES 密钥
keyId = SHA256( key 的字节 )[:16]                           # 16 位小写 hex
```

#### AAD 构造（复刻 `buildAuthenticatedContextAad`，sym-v1 / framing=field）

```
AAD_DOMAIN = b"WB-AAD\x00"
FRAMING_CODE = {"file":1,"field":2,"record":3,"stream":4}
STANDARD_FORMAT_ID = {"file":"WBEF1","field":"WBEV1","record":"WBER1","stream":"WBES1"}

encodeUint32(v)        = v 的 4 字节大端
encodeLengthPrefixed(s) = 4 字节大端长度 + s 的 UTF-8 字节

AAD = AAD_DOMAIN
    + [1]                            # 版本
    + encodeLengthPrefixed("WBEV1")   # 标准格式 id（field）
    + encodeLengthPrefixed("sym-v1")  # 套件
    + encodeUint32(1)                 # keyId 计数
    + encodeLengthPrefixed(keyId)     # 16 位 hex 字符串
    + [2]                            # framing = field
    + [0]                            # sequence 缺失占位
    + [0]                            # final 缺失占位
```

#### 加密 / 解密（AES-256-GCM）

- 算法：AES-256-GCM，`nonce` 12 字节，`authTag` 16 字节（GCM 默认 tag 长度）。
- **计数器偏移（易错点）**：数据块的计数器从 `inc32(J0)` 开始，即 `nonce || 0x00000002`（J0 = `nonce || 0x00000001` 仅用于给 tag 做 `E_K(J0)`）。数据块 `i`（从 1 起）的计数器为 `nonce || (i + 1)`。
- 密文：`C_i = P_i XOR E_K(nonce || (i+1))`。
- 标签：`T = S XOR E_K(J0)`，其中 `S = GHASH(AAD || 密文 || 长度块)`，长度块 = `len(AAD)*8`（8 字节大端）+ `len(密文)*8`（8 字节大端）。GHASH 以 `H = E_K(0^128)` 为基，标准 GF(2^128) 乘法（约减多项式 `x^128 + x^7 + x^2 + x + 1`）。
- 解密时 GCM 完整性校验严格：密文 / authTag / AAD 任一被篡改都会被拒绝。

#### 解密密钥（atRestSecretKey）获取

**结论**：该密钥（44 字符规范 base64）**不存在于任何文件**，由运行中的客户端在运行时提供，**不落盘**；同一把密钥既直接加密 `auth` 字段，也用于包裹 `~/.workbuddy/keyblob` 里的主密钥。脚本按以下顺序取得：

1. 环境变量 `WORKBUDDY_ATREST_KEY` —— 直接给 44 字符密钥串（最省事，推荐）。
2. 环境变量 `WORKBUDDY_ATREST_KEY_FILE` —— 指向含明文密钥，或由当前用户加密保存的密钥块的文件。
3. 登录态所在 `Data` 目录下由客户端加密保存的密钥块，用当前用户身份解开，并用信封 `keyId` 校验派生匹配（兼容个别版本把密钥落盘的情况）。
4. **运行中的客户端提供的密钥（主路径）**：
   - 当客户端**已启动并登录**、且脚本与客户端在同一用户下运行时，脚本可直接取得该密钥。
   - 可用环境变量 `WORKBUDDY_PROCESS_NAMES` 指定客户端进程名（逗号分隔）；未指定时按默认列表自动识别。
   - 取得后按 `key = SHA256(密钥串)`, `keyId = SHA256(key)[:16]` 校验匹配，不匹配则继续下一个候选。
   - 前提与限制：**仅 Windows 提供该途径**；脚本与客户端须在同一用户下运行（客户端提权而脚本未提权时会被系统拒绝）；密钥只在本地校验使用，**绝不写盘、绝不打印**。
5. macOS 上以上本地途径均不可用，系统凭据库中也没有对应条目 → **改用第 7 节的网页会话授权**。

#### AES 后端优先级

主实现优先 `cryptography`（`AES-GCM`）；缺失回退 PyCryptodome；两者都缺失则使用内置**纯 Python AES-256-GCM**（标准 S-Box + GF(2^128) GHASH + CTR）。三层实现已交叉对拍，且 `cryptography` 与纯 Python 互验一致；运行 `python scripts/workbuddy_checkin.py --self-test-atrest` 可离网验证。

## 2. 签到接口

基础地址：`https://<auth.domain>/v2`

### 2.1 状态查询（只读）

```
POST /v2/billing/meter/checkin-activity-status
Authorization: Bearer <accessToken>
Content-Type: application/json
```

响应（示例）：

```json
{
  "code": 0,
  "data": {
    "today_checked_in": false,
    "streak_days": 12,
    "daily_credit": 100,
    "total_credits": 1340
  }
}
```

> 余额字段是 **`total_credits`（复数）**；早期脚本只认单数 `total_credit`，导致 `balance` 恒为 `null`。详见第 5 节候选名清单。

已签到的判定（脚本兼容多种形态）：
- `today_checked_in == true`
- `data.today_checked_in == true`
- `code == "10001"`

### 2.2 领取签到

```
POST /v2/billing/meter/daily-checkin
Authorization: Bearer <accessToken>
Content-Type: application/json
```

成功响应：

```json
{
  "code": 0,
  "credit": 100,
  "streak_days": 13,
  "data": { "credit": 100, "daily_credit": 100 }
}
```

已签到响应（幂等，非错误）：

```json
{
  "code": 10001,
  "msg": "今天已签到，请明天再来"
}
```

---

## 3. 派猫猫旅行接口

基础地址：`https://www.workbuddy.cn`

> **与签到不是同一个域名，且路径不带 `/v2` 前缀**——这是最容易踩的坑。
> 用登录态里的 `auth.domain`（`www.codebuddy.cn`）或误加 `/v2` 都会 404。
> 旅行接口仅需 Bearer Token，**无需** Turing Shield 设备指纹。

### 3.1 旅行状态（只读）

```
GET /activity/growth/buddy/travel/status
Authorization: Bearer <accessToken>
```

响应示例：

```json
{
  "code": 0,
  "data": {
    "state": "idle",
    "location": { "id": 1, "name": "咖啡馆" },
    "reward_credit": 0,
    "arrive_at": 1757500000,
    "server_now": 1757490000,
    "daily_limit_reached": false,
    "record_id": 123
  }
}
```

状态三态：

| `state` | 含义 | 可执行动作 |
|---|---|---|
| `idle` | 空闲 | 未达每日上限时可 `depart` |
| `traveling` | 旅行中 | 无；用 `arrive_at - server_now` 算到达倒计时 |
| `arrived` | 已到达，待领取 | 可 `claim` 领取积分 |

### 3.2 领取旅行积分（写）

```
POST /activity/growth/buddy/travel/claim
Authorization: Bearer <accessToken>
Content-Type: application/json

{}
```

成功：`{"code":0,"data":{"reward_credit":8}}`。
无可领取时服务端返回非 0 码，脚本如实上报、绝不编造结果。

### 3.3 派出 Buddy（写）

```
POST /activity/growth/buddy/travel/depart
Authorization: Bearer <accessToken>
Content-Type: application/json

{"location_id": 1}
```

成功：`{"code":0,"data":{"location":{"id":1,"name":"咖啡馆"},"arrive_at":1757500000}}`。

### 3.4 可选地点（只读）

```
GET /activity/growth/buddy/travel/config
```

返回 `data.locations`（`id` / `name` / `duration_hours_min` / `duration_hours_max` / `reward_credit_min` / `reward_credit_max`）。
四个地点为**咖啡馆 / 商场店铺 / 健身房 / 古镇客栈**，时长与积分区间完全相同（随机 1-4 小时、5-10 积分），**收益无差异**，`location_id` 缺省时随机选一个。

### 3.5 派遣前置检查（硬规则）

调用 `depart` 前必须先读 `status` 并同时满足：

1. `state == "idle"`
2. `daily_limit_reached` 为假

任一不满足即跳过派遣，**不发任何写请求**。

---

## 4. 错误码 / 状态码对照

| HTTP | code | 含义 | 脚本处理 |
|------|------|------|----------|
| 200 | 0 | 领取成功 | `action=clicked`，展示积分/连续天数 |
| 400 | 10001 | 当天已签 | `action=skip_already_signed`（幂等跳过，非失败） |
| 非 2xx | — | 网络/服务端错误 | 读取响应体；若含"已签到"仍判为已签，否则 `status=error` |
| — | — | `accessToken` 缺失/过期 | `status=error`，提示重新登录客户端 |

> 脚本对非 2xx 也解析响应体，避免把"已签到 400"误判为异常。
>
> **旅行接口的降级约定**：只读接口（`status` / `config`）失败时置 `travel.available=false` 并静默跳过，
> 绝不改变签到结论；写接口（`claim` / `depart`）返回非 0 码时，把服务端 `msg` 如实记入
> `travel.auto_log`，同样不影响签到状态与退出码。

---

## 5. 积分余额字段候选名

不同版本接口返回的余额字段名不统一，脚本按以下候选名 + 嵌套层级兜底提取，写入结果 `balance` 字段（找不到则返回 None，不影响签到）：

- 候选键（**注意复数形态**，接口返回的是 `total_credits`（复数），早期版本脚本只认单数导致余额恒为空）：
  `total_credits` / `total_credit` / `total_credit_balance` / `credits` / `total_points` / `points_balance` /
  `credit_balance` / `balance` / `remain_credit` / `remain_credits` / `remain` / `score` / `integral` /
  `totalCredits` / `totalCredit` / `pointsBalance` / `balanceCredit`
- 候选层级：`（顶层）` / `data` / `result` / `data.result`

---

## 6. 消息推送模块接口（`scripts/push_message.py`，可选，密钥仅本地）

仅当用户主动配置 `notify_config.json` 时，才会向对应渠道发请求。凭据不进入脚本或技能目录。
模块**纯 Python 标准库**实现（urllib / json / hmac / hashlib / smtplib / subprocess），可独立运行。

### 6.1 渠道注册表与构造器

```python
CHANNELS = {"dingtalk": build_dingtalk, "feishu": build_feishu, "wecom": build_wecom,
            "wechat": build_wechat, "email": build_email, "sms": build_sms, "qq": build_qq,
            "slack": build_slack, "telegram": build_telegram, "bark": build_bark,
            "webhook": build_webhook}          # 11 个走网络/邮件的渠道
SUPPORTED   = [...11 项..., "system"]          # 对外宣称支持的 12 类
ZERO_CONFIG = {"system"}                       # 无需任何凭据
PAID_CHANNELS = {"sms"}                        # 付费渠道，需 confirm_paid=True 才放行
```

`system` 不进入 `CHANNELS`，由 `send_one` 开头短路处理（`if channel == "system": return send_system(...)`）。

每个 `build_<channel>(cfg, msg)` 统一返回下列两种形态之一，由 `send_one` 分发：

| 返回 | 含义 |
|---|---|
| `("http", url, payload, headers)` | POST JSON 到 `url`（`headers` 可为 `None`） |
| `("smtp", cfg, msg)` | 走 `smtplib`（仅 `email`） |

必填项缺失时抛 `ConfigError`，由上层转为 `unconfigured`，**不发起任何网络请求**。
未知渠道名由 `send_one` 直接返回 `failed`（`detail="未知渠道 X"`）。

### 6.2 配置归一化

`normalize_channels(cfg)` 把两种写法**按渠道合并**（同渠道以新结构为准，旧字段只补齐新结构里没有的渠道）：

- 新结构：`{"channels": {"dingtalk": {"webhook": ...}, "email": {...}}}`
- 旧扁平字段（向后兼容，仅微信三通道）：
  - `wecom_webhook` → `wecom.webhook`
  - `pushplus_token` → `wechat.pushplus_token`
  - `bark_url` → `bark.server` + `bark.device_key`（由 `_split_bark_url` 自动拆分）

**为什么是合并而不是二选一**：若写成「出现 `channels` 就整体忽略旧字段」，老用户只想新增一个渠道（如在 `channels` 里加 `email`）时，原有企业微信 / PushPlus / Bark 推送会静默失效。合并语义避免了这类回归。

### 6.3 渠道接口清单（与实现一一对应）

| 渠道 | 地址 | 形态与要点 |
|------|------|------|
| `dingtalk` | 用户填的 `webhook` | POST markdown；填 `secret` 时追加 `&timestamp=<毫秒>&sign=<HMAC-SHA256>` |
| `feishu` | 用户填的 `webhook` | POST `msg_type=text`；填 `secret` 时追加 `timestamp=<秒>&sign=` |
| `wecom` | 用户填的 `webhook`（`https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<KEY>`） | POST markdown，正文前缀 `# 标题`；填 `secret` 时追加毫秒级签名 |
| `wechat` | `https://www.pushplus.plus/send` | POST，`token` + `template`（markdown/html） |
| `email` | `smtp_host:smtp_port` | `smtplib`；默认 465 + SSL，`smtp_mode=starttls` 可切；`smtp_pass` 为授权码 |
| `sms` | 用户填的 `url` + `payload_template` | POST（**付费**，默认拦截）；支持 `headers` |
| `qq` | 用户填的 `url` | 直接复用 `build_webhook` |
| `slack` | 用户填的 `webhook_url` | POST，正文 `*标题*\n正文` |
| `telegram` | `https://api.telegram.org/bot<token>/sendMessage` | POST，`chat_id` + `text` |
| `bark` | `<server>/push` | POST `{"device_key","title","body"}`；也接受整条 `bark_url`（自动拆分） |
| `webhook` | 用户填的 `url` | POST；`payload_template` 支持 `{{title}}` / `{{content}}` 占位符；可选 `headers` |
| `system` | 本地 `~/.workbuddy/scripts/notifications.jsonl` + 桌面 toast | 零配置、离线可用，不联网 |

### 6.4 对外函数

| 函数 | 作用 |
|---|---|
| `load_raw_config(path=None)` | 读取配置，回退顺序：`path` → 环境变量 `WORKBUDDY_CHECKIN_PUSH_CONFIG` → 默认 `~/.workbuddy/scripts/notify_config.json`；均不存在返回 `None` |
| `normalize_channels(cfg)` | 归一化新旧两种结构 |
| `resolve_channels(cfg, requested=None)` | 决定本次实际目标渠道集合（`requested` 非空则覆盖） |
| `send_one(channel, cfg, msg, timeout)` | 发单渠道，返回 `{"channel","status",...}` |
| `send_message(title, content, channels=None, content_type="markdown", confirm_paid=False, timeout=10, config_path=None)` | 主入口，聚合并返回 `{"ok": bool, "results": [...]}` |
| `ready_channels(config_path=None)` | 只读列出 `ready` / `unconfigured`，供 `--diagnose` 使用（不联网） |
| `write_sample(config_path=None)` | 生成双结构配置模板 |
| `hmac_sign(secret, timestamp)` | 钉钉 / 飞书 / 企业微信加签 |
| `desktop_toast(title, content)` | 跨平台桌面通知（`system` 渠道使用） |

### 6.5 结果状态与错误边界（硬约定）

| `status` | 含义 |
|---|---|
| `success` | 发送成功（HTTP 2xx，或邮件已投递） |
| `failed` | 发送失败（网络异常 / 凭据失效 / URL 非法），`detail` 为该渠道异常摘要（截断 500 字符） |
| `unconfigured` | 必填项缺失（`ConfigError`），**未发起请求** |
| `skipped` | 主动跳过：付费渠道未确认、`enabled=false`、未配置任何渠道时的 `<none>` 占位 |

- **单渠道失败隔离**：任一渠道异常只记入该条 `results`，`try/except` 包裹，绝不中断其他渠道，也绝不改变签到 `status` 与退出码。
- **判定顺序**（`send_message` 内，务必照此理解结果）：
  1. 逐个渠道判「是否已配置」——`ch in configured or ch in ZERO_CONFIG` 才进入发送目标；显式请求但未配置的 → 直接记 `unconfigured`（**未发起请求**）。
  2. 再对付费渠道做闸门——目标里含 `PAID_CHANNELS` 且 `confirm_paid=False` → 记 `skipped`，并从目标中剔除。
  3. 最后逐个发送，异常统一转 `failed`。
  因此：**未配置的 `sms` 会显示 `unconfigured`，已配置但未加 `--confirm-paid` 才显示 `skipped`**。
- **凭据脱敏**：`detail` 只含渠道名与错误摘要，**永不回显 webhook / token / 密码**。
- 配置文件不存在 → 返回 `{"ok": True, "skipped": True, "results": [{"channel":"<none>","status":"skipped",...}]}`；`enabled=false` 且未显式指定渠道时同样静默跳过。主脚本对这类返回静默处理，用户无感知。
- `ok` 语义：所有 `results` 均为 `success` 才为 `True`；主脚本**不据此改变签到退出码**。

配置字段与获取方式见同目录下的 `examples.md`（不要在本文件里用加载式引用）。

## 7. 网页会话授权凭据（通道 B）

用于第 1 节所述「本机登录态无法在本地解密」的场景（典型为 macOS）。凭据**只能由用户主动提供**，脚本不自行查找或提取。

### 7.1 凭据来源与本地存储

| 来源 | 输入形态 | 是否自带原始 UA |
|---|---|---|
| 网页授权码（推荐） | `WBAUTH1:` + base64url(JSON) | 是 |
| Copy as cURL | 开发者工具复制的整段命令 | 是 |
| HAR 文件 | 用户导出的 `.har` | 是 |
| 裸凭据粘贴 | Cookie 串或 Bearer 令牌 | 否（用内置默认 UA 兜底） |

授权码的 JSON 载荷固定为：

```json
{"v":1,"mode":"cookie|bearer","value":"<凭据>","ua":"<原始 User-Agent>","domain":"<站点 origin>"}
```

存储位置：`~/.workbuddy/checkin-cred.json`（POSIX 下 `0600`），字段：

```json
{"mode":"cookie|bearer","value":"<凭据>","user_agent":"<UA>","checkin_domain":"<生效域名>",
 "source":"auth-code|curl|har|paste|bookmarklet","saved_at":<epoch秒>,"version":"3.1.7"}
```

### 7.2 鉴权请求头（与网关会话绑定相关）

- `mode=cookie` → `Cookie: <value>`
- `mode=bearer` → `Authorization: Bearer <value>`
- 固定附带：`Content-Type: application/json`、`Accept: application/json, text/plain, */*`、`Accept-Language: zh-CN,zh;q=0.9,en;q=0.5`、`X-Client-Platform: web`、`Origin: <base>`、`Referer: <base>/`
- **`User-Agent` 必须回放凭据所属会话的原始 UA**：官方网关把会话与 UA 绑定，UA 不一致会得到 401。这正是授权码 / cURL / HAR 三种方式都要携带 UA 的原因。

### 7.3 端点与候选顺序

- 签到相关路径与第 2 节一致（`/v2/billing/meter/...`），域名按「凭据自带 origin → `https://www.workbuddy.cn` → `https://www.codebuddy.cn`」顺序探测，命中即用。
- 旅行相关路径与第 3 节一致（`https://www.workbuddy.cn`，不带 `/v2`）。
- 解析出的 cookie / bearer 两种候选会**依次校验、取首个通过者**（cookie 优先）。

### 7.4 状态与退出码

| `status` | 含义 | 处理 |
|---|---|---|
| `ok` | 校验通过或执行成功 | 正常 |
| `no_cred` | 本机尚未授权 | 引导用户做一次性授权 |
| `auth_failed` | 凭据无效或已过期（HTTP 401/403） | 提示重新授权一次 |
| `error` | 其他失败 | 如实报告 `msg` |

退出码：`ok` → 0；其余 → 1。授权命令（`--setup-*`）成功 → 0，失败 → 1，且**失败时不落盘**。

## 8. 长效令牌刷新接口（通道 C，v3.1.7 新增）

通道 C 使用用户自己的长效令牌（refresh token）向官方插件网关换取新的接口令牌。

### 8.1 刷新端点

- `POST https://copilot.tencent.com/v2/plugin/auth/token/refresh`
- 请求头：
  - `Content-Type: application/json`
  - `X-Refresh-Token: <长效令牌>`
  - `X-Auth-Refresh-Source: plugin`
  - `X-Domain: copilot.tencent.com`
- 请求体：`{}`
- 成功：`{"code":0,"data":{"accessToken":"<新接口令牌>","refreshToken":"<轮换后的新长效令牌>",...}}`
- 失败：`code != 0`（令牌失效 / 服务端会话过期），脚本提示重新导出或录入一次。

### 8.2 轮换语义（实测）

- 每次刷新都会返回新的长效令牌（轮换）；脚本把新令牌回写本机凭据库实现自续。
- **旧的长效令牌不会立即失效**：回写失败时旧令牌仍可用作兜底；本通道与客户端各自的续期互不干扰。
- 副作用：已泄露的旧令牌在服务端会话过期前持续可用 —— 令牌等同账号钥匙，务必妥善保管、勿分享。

### 8.3 刷新所得接口令牌的接受范围（实测）

- 可用于第 2 节签到接口（`https://www.codebuddy.cn/v2/billing/meter/...`）。
- 可用于第 3 节派猫猫旅行接口（`https://www.workbuddy.cn/activity/growth/buddy/travel/...`）。
- 刷新接口本身不校验 User-Agent（与 7.2 网页网关的会话绑定机制不同），因此适合云端/服务端定时场景。

### 8.4 本地存储

- 存储位置：`~/.workbuddy/checkin-rt.json`（POSIX 下 `0600`），字段：

```json
{"v":1,"rt":"<长效令牌>","source":"export|manual|refresh","saved_at":<epoch秒>,"version":"3.1.7"}
```

- 轮换回写：每次刷新成功后把新长效令牌写回该文件（`source` 置为 `refresh`）；写失败不影响本次签到。
- 清除：`--forget` 置空覆盖（不删除文件），与通道 B 约定一致。

### 8.5 状态与退出码

| `status` | 含义 | 处理 |
|---|---|---|
| `ok` | 刷新并执行成功 | 正常 |
| `need_setup` | 本机尚未保存长效令牌 | 引导 `--export-rt --save` 或 `--setup-rt` |
| `error` | 刷新失败或其他失败 | 刷新失败提示重新导出/录入；其余如实报告 `msg` |

退出码：`ok` → 0；其余 → 1。
