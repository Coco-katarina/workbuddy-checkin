# workbuddy-checkin

> WorkBuddy（原 CodeBuddy）「Buddy 加油站」每日积分自动签到 Skill —— 接口直签、三通道授权、跨平台（Windows / macOS / Linux），零第三方依赖。

核心实现来自 **totorosir-workbuddy-checkin v3.1.7**（SkillHub，@totorosir / 公众号「龙猫科技说」，已经腾讯科恩实验室与云鼎实验室双重安全审计），本仓库在其基础上做了适配性优化与进阶工具补充。

## 能做什么

- **每日签到**：自动领取 100 积分（连续第 7 天 1000 积分），今日已签自动跳过（幂等）
- **派猫猫旅行**：先领后派全自动闭环（到达自动补领、每日上限不发请求）
- **桌面通知 + 12 渠道推送**：钉钉 / 飞书 / 企业微信 / 微信 / 邮件 / Bark / Telegram 等（可选）
- **客户端不开也能签**：长效令牌（refreshToken）自续期，定时任务完全独立运行

## 三条授权通道（统一入口 `scripts/auto_checkin.py` 自动择一）

| 通道 | 适用场景 | 配置成本 |
|---|---|---|
| A · 本地登录态直签 | Windows（客户端运行中即可解密） | **零配置** |
| B · 网页会话授权 | macOS 等无法本地解密的环境 | 一次性授权约 30 秒 |
| C · 长效令牌自续期 | 任意平台；客户端不常开 / 独立定时任务 | 一次性录入令牌 |

探测当前环境走哪条通道：

```bash
python scripts/auto_checkin.py --probe
```

### macOS 快速开始（通道 C，推荐）

1. 导出一次长效令牌（客户端已登录时自动读取；加密登录态下需先解密，见下文进阶工具）：

   ```bash
   python scripts/rt_auth.py --export-rt --save
   ```

2. 验证（只读，不领取）：

   ```bash
   python scripts/rt_auth.py --check
   ```

3. 之后每天运行 `python scripts/rt_auth.py` 即可，令牌自动轮换回写、长期有效。

### 通道 B（网页授权，一次性）

```bash
python scripts/web_auth.py --snippet          # 打印授权码工具与三步操作
python scripts/web_auth.py --setup-code '<授权码>'   # 保存并校验
```

凭据只存本机 `~/.workbuddy/checkin-cred.json`（0600），输出全程脱敏。

## 本仓库相对上游的优化

| 优化 | 说明 |
|---|---|
| `.gitattributes` | 强制 `*.sh/*.js/*.py` 以 LF 提交（修复上游以 CRLF 打包导致 macOS/Linux 脚本无法执行的问题），`*.ps1` 保持 CRLF |
| `tools/decrypt-token.js` | **进阶工具**：客户端 5.6.2+ 登录态为 AES-256-GCM 信封、解密密钥不落盘，本工具经 CDP（`--inspect` 短暂重启客户端，约 30 秒后自动恢复）在进程内提取 `atRestSecretKey` 并解密出 `refreshToken`，配合通道 C 实现免网页授权。含 Windows `ELECTRON_RUN_AS_NODE` 环境剥离、CDP 发现双通道（`/json/list` + 启动日志解析）、陈旧 socket 防误杀等修复 |
| AAD 布局 | 从客户端源码逆向确认信封 AAD 公式（`WB-AAD\0` + suite/keyId/framing 结构），解密前按 `keyId = SHA256(key)[:16]` 严格校验 |
| 文档 | 补充 macOS / 通道 C 快速开始与接口域名说明（签到走 `auth.domain`，旅行走 `www.workbuddy.cn` 且不带 `/v2`） |

## 进阶：macOS 加密登录态导出 refreshToken（可选）

适用于不想走网页授权、愿意让客户端短暂重启一次的用户：

```bash
node tools/decrypt-token.js
```

成功后输出 `DECRYPT_RESULT:<refreshToken>`（请自行妥善保管，勿外传），然后：

```bash
python scripts/rt_auth.py --setup-rt-file <令牌文件>
```

> 注意：该工具需要 Node.js ≥ 18，会重启一次 WorkBuddy 客户端（约 30 秒）；完成后正常启动。若失败请改用通道 B（网页授权）。

## 定时自动化

推荐使用 WorkBuddy 自带自动化（每日 09:00），不要用 crontab / launchd。

## 安全约束

- 令牌 / 凭据只存本机（`~/.workbuddy/checkin-rt.json` / `checkin-cred.json`，0600），任何输出脱敏
- 只访问 WorkBuddy 官方域；写操作仅限 4 个已验证端点
- `--export-rt` 输出的 refreshToken 等同账号钥匙，请勿提交到仓库或分享

## 致谢与来源

- 上游技能：[totorosir-workbuddy-checkin](https://skillhub.cn/skills/user_3a370331/totorosir-workbuddy-checkin)（SkillHub，作者 totorosir），版权归原作者所有
- 本仓库历史版本（safeStorage / 明文登录态方案）与详细变更见 [CHANGELOG.md](CHANGELOG.md)
