# 变更日志

## [3.0.0-fork] - 2026-10-09

> 本版本起以 totorosir-workbuddy-checkin v3.1.7（SkillHub，作者 totorosir，已通过科恩/云鼎实验室安全审计）为核心重写，原 2.x 的自行解密方案转为 `tools/decrypt-token.js` 进阶工具。已在 macOS（加密登录态 + 通道 C）实测通过。

### 新增

- **三通道授权**（统一入口 `scripts/auto_checkin.py`）：A 本地登录态直签（Windows 零配置）/ B 网页会话授权（macOS）/ C 长效令牌自续期（refreshToken 自动轮换回写，客户端不开也能签）。
- **派猫猫旅行闭环**：先领后派、每日上限不发请求、到达自动补领。
- **12 渠道消息推送** + 桌面通知（可选，`notify_config.json` 本地配置）。
- **`tools/decrypt-token.js`**（进阶）：macOS 上经 CDP 提取 `atRestSecretKey` 解密登录态、导出 refreshToken 配合通道 C，免网页授权。相对 2.0.0 的改进：
  - CDP 发现双通道：`/json/list` 失败时回退解析启动日志中的 `Debugger listening on ws://...`（Electron 37 主进程 inspector 的 `/json/list` 会异常断连，已实测）；
  - 已有调试端口就绪时直接复用，不再强制重启；`/json/list` target 出现前轮询等待（此前单次探测会在启动后 1 秒误判不可用）；
  - 启动客户端前剥离 `ELECTRON_RUN_AS_NODE` / `NODE_OPTIONS`（沙箱/受管环境下该变量会导致客户端以纯 Node 模式启动后秒退）；
  - darwin 下 osascript 被沙箱拒绝（-10004）时，从 `workbuddy-sandbox-center-*.sock` 文件名解析主进程 PID 强杀，并校验 socket mtime（10 分钟内）与 PID 存活性，防止陈旧 socket 误杀无关进程；
  - 解密成功后 accessToken 可缓存至 `~/.workbuddy/tools/checkin-token-cache.json`（0600，28 天内免重启），`WB_CHECKIN_CACHE=0` 关闭。

### 修复

- 上游打包 CRLF 导致 macOS/Linux 脚本无法执行：新增 `.gitattributes` 强制 LF（`*.ps1` 除外）。

### 变更

- 签到接口统一以登录态 `auth.domain` 为准（`/v2/billing/meter/*` + `X-User-Id`）；旅行接口走 `www.workbuddy.cn` 且不带 `/v2`。
- 移除 2.x 的 `checkin.sh` / `checkin.ps1` / `setup.sh` / `setup.ps1`（由三通道 Python 脚本替代）。

## [2.0.0] - 2026-10-09

### 新增

- **适配新版桌面端加密登录态（AES-256-GCM 信封）**：v5.3.8+ 后期版本把 `auth.accessToken` 存为 `{ $wbEncrypted: 1, envelope }` 加密信封（此前 1.x 假设明文，会报「未找到新版明文认证文件」）。`decrypt-token.js` 现自动分流：
  - 明文字符串（旧版桌面端）→ 纯 Node 直读；
  - 加密信封（新版桌面端）→ 解密流程：数据密钥 = `sha256(atRestSecretKey)`，`atRestSecretKey` 经客户端原生绑定 `workbuddyStorage.loggerGet()` 在进程内获取（带 `--inspect` 短暂重启客户端，CDP 读取，全程自动约 30 秒，完成后自动恢复正常启动）；
  - AAD 布局已从客户端源码逆向确认（`"WB-AAD\0" + [1] + u32"WBEV1" + u32"sym-v1" + u32(1) + u32(16)keyId + [2] + [0] + [0]`），无需暴力枚举；解密前校验 `keyId = sha256(key) 前 16 位 hex`。
- **本地令牌缓存**（默认开启）：解密成功后 accessToken 写入 `~/.workbuddy/tools/checkin-token-cache.json`（权限 600），28 天有效期内定时签到免重启客户端；`WB_CHECKIN_CACHE=0` 关闭。
- **Windows 自动重启客户端支持**：加密信封分支自动探测客户端可执行文件（`%LOCALAPPDATA%\Programs\WorkBuddy\WorkBuddy.exe` 等），可用 `WB_CHECKIN_APP_BIN` 指定。

### 修复

- **打包换行符 CRLF 导致 macOS/Linux 脚本完全无法执行**（`bash: pipefail: invalid option name`）：仓库此前以 CRLF 提交 `.sh/.js`。已统一为 LF，并新增 `.gitattributes`（`*.ps1` 保持 CRLF，其余 LF），从源头杜绝复发。

### 改进

- API 统一为 `/v2/billing/meter/*` + `X-User-Id` 等鉴权头（对齐桌面端 `buildHeaders`），`checkin.sh` 与 `checkin.ps1` 行为一致。
- `setup.sh` / `setup.ps1` 改为轻量环境体检（识别登录态形态：明文 / 加密信封 / 旧版 vscdb），不再触发解密。
- 新增环境变量：`WB_CHECKIN_APP_BIN`、`WB_CHECKIN_INSPECT_PORT`、`WB_CHECKIN_CACHE`。

## [1.0.3] - 2026-08-12

### 修复

- **Windows 读不到新版明文登录态导致持续 401**：`decrypt-token.js` win32 候选优先 `%LOCALAPPDATA%`、回退 `%APPDATA%`。
- **假 401 导致当日签到被跳过**：401 判定改用真实 HTTP 状态码（`curl -w '\n%{http_code}'`），不再扫响应体子串（随机 UUID 含 "401" 的误判率 0.566%/次）。

### 改进

- 请求签名对齐桌面端 `buildHeaders`（`checkin.ps1` 走 `/v2/` + `X-User-Id`）；`decrypt-token.js` 成功分支追加输出 `ACCOUNT_UID` / `AUTH_DOMAIN` / `ENTERPRISE_ID`（调用方必须按行前缀过滤）。

## [1.0.2] - 2026-08-01

- 适配 v5.3.8+ 新版明文登录态文件（`workbuddy-desktop.info`），Node 直读优先，旧版 `state.vscdb` 回退；`code=10001` 识别为「今日已签到」。

## [1.0.0/1.0.1] - 2026-07

- 首版：Electron safeStorage 解密旧版 `state.vscdb` + 官方签到 API，macOS/Windows/Linux。
