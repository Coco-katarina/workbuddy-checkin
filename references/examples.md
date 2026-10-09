# 示例与配置（WorkBuddy签到助手）

本文件供 Skill 执行与用户配置参考，包含：自动化提示词、命令示例、多渠道消息推送配置、安装/卸载说明。

---

## 1. WorkBuddy 自带自动化提示词

创建 recurring 自动化（`automation_update`，mode=create）时，把下面这段作为自动化 `prompt`：

```
请使用 Bash 工具运行以下命令，完成 WorkBuddy签到助手（每日自动签到 + 派猫猫旅行全自动闭环，接口直签，无需点击 GUI）。
若 `python` 命令不可用，改用 WorkBuddy 自带托管 Python：
%USERPROFILE%/.workbuddy/binaries/python/versions/*/python
python "%USERPROFILE%/.workbuddy/scripts/auto_checkin.py"

该入口会自动在本机登录态通道与网页授权通道之间择一，你无需判断走哪条。
若输出 status=no_cred / action=need_setup（典型为 macOS：本机登录态无法在本地解密）：
- 不要重复重试，也不要在本机寻找解密密钥；
- 运行 python "%USERPROFILE%/.workbuddy/scripts/web_auth.py" --snippet，把「授权码工具 + 三步操作」原样转述给用户；
- 用户贴回授权码后执行 python "%USERPROFILE%/.workbuddy/scripts/web_auth.py" --setup-code '<授权码>'；
  若用户反馈读不到会话，改引导 Copy as cURL 或导出 HAR，分别用 --setup-curl / --setup-har；
- 授权成功后再跑一次本入口即可完成签到。

执行后，根据脚本输出的 JSON 结果，用一句话向用户汇报：
- action=clicked：签到成功，已领取积分（说明 +N 积分、连续第几天；若结果含 balance 字段则附「当前积分余额：XXX」）。
- action=skip_already_signed：今日已签到，无需重复操作（若含 balance 字段一并告知余额）。
- action=skip_check_only：仅查询完成，汇报今日是否已签与余额。
- action=travel：只跑了派猫猫旅行（未签到），汇报旅行状态即可。
- status=error：如实报告 msg 中的失败原因，不得谎报成功。

再读结果里的 `travel` 字段，补一句派猫猫旅行情况：
- state=traveling：Buddy 正在【location_name】旅行，还需 remaining_text 到达。
- state=arrived：Buddy 已到达，可领 reward_credit 积分（若 auto_log 显示已领取则说明领取结果）。
- state=idle 且 daily_limit_reached=true：今日派遣次数已用完，跳过派遣（正常，不是故障）。
- 若 travel.available=false：旅行接口本次不可用，直接说明「旅行状态未能获取」，不要编造状态。
- auto_log 逐条列出本次实际动作（领取 / 派遣 / 跳过原因），照实转述。

约束：
1. 严禁在任意输出中打印 token / accessToken / refreshToken，也不要回显任何推送凭据（webhook / smtp_pass / pushplus_token / bot_token）。
2. 不要无限重试；脚本内部最多重试 1 次即可。
3. 若命令执行失败（status=error），脚本会自动向已配置渠道推送失败提醒（前提是已配置 notify_config.json）；你仍需在对话中如实报告失败原因，并提示检查 WorkBuddy 是否已登录、电脑是否联网、是否在 09:00 前后保持开机且客户端未退出。
4. 若推送未配置或未收到，不要谎报"已通知"。可先跑 `--diagnose` 看 `notify_config.ready`，并如实转述：例如"未配置任何推送渠道，请检查 notify_config.json"或"渠道 X 未配置（unconfigured）"。
5. 推送失败不得影响签到结论与退出码；汇报时把推送状态与签到状态分开说。
6. 不要擅自为推送渠道加付费项（如短信 sms），也不要自动加 `--confirm-paid`。
7. 严禁回显用户贴回的授权码 / cURL / HAR 内容或任何网页凭据；授权完成后不要重复粘贴。
8. 通道 B 的授权凭据只存本机 `~/.workbuddy/checkin-cred.json`，不要写入技能目录、不要随包分发、不要提交到仓库。
```

> 路径统一用 `%USERPROFILE%`（Windows）/ `~`（macOS、Linux），无需替换用户名。推荐 rrule：`FREQ=DAILY;BYHOUR=9;BYMINUTE=0`（每天 09:00）。

---

## 2. 命令示例

Windows 运行（用正斜杠路径，避免 Git Bash MSYS 把 `/c/` 当相对路径）：

```bash
# --- 日常（推荐用统一入口，自动择通道）---
python "%USERPROFILE%/.workbuddy/scripts/auto_checkin.py"                       # 签到 + 旅行全自动闭环（默认）
python "%USERPROFILE%/.workbuddy/scripts/auto_checkin.py" --probe               # 只读：报告该走哪条通道
python "%USERPROFILE%/.workbuddy/scripts/auto_checkin.py" --check --no-notify   # 仅查询（全只读）
python "%USERPROFILE%/.workbuddy/scripts/workbuddy_checkin.py" --no-travel      # 强制通道 A：只签到，跳过旅行

# --- 派猫猫旅行 ---
python "%USERPROFILE%/.workbuddy/scripts/workbuddy_checkin.py" travel                 # 只查旅行状态（只读）
python "%USERPROFILE%/.workbuddy/scripts/workbuddy_checkin.py" travel --travel-auto    # 只跑旅行闭环（不签到）
python "%USERPROFILE%/.workbuddy/scripts/workbuddy_checkin.py" --location 3            # 指定派去健身房

# --- 消息推送 ---
python "%USERPROFILE%/.workbuddy/scripts/workbuddy_checkin.py" --push-channels dingtalk,email  # 本次仅推指定渠道
python "%USERPROFILE%/.workbuddy/scripts/workbuddy_checkin.py" --confirm-paid                  # 放行付费渠道（短信）
python "%USERPROFILE%/.workbuddy/scripts/push_message.py" --title "测试" --content "hello"     # 独立测试（发全部已配置渠道）
python "%USERPROFILE%/.workbuddy/scripts/push_message.py" --title T --content C --channels system  # 只发系统通知（零配置、离线）
python "%USERPROFILE%/.workbuddy/scripts/push_message.py" --ready                              # 只列出已就绪渠道（只读，不发送）

# --- 网页授权（通道 B：本机登录态不可用时，一次性授权）---
python "%USERPROFILE%/.workbuddy/scripts/web_auth.py" --snippet                 # 打印授权码工具与用法
python "%USERPROFILE%/.workbuddy/scripts/web_auth.py" --setup-code '<授权码>'  # 保存授权码（- 从 stdin 读）
python "%USERPROFILE%/.workbuddy/scripts/web_auth.py" --setup-curl '<cURL>'    # 保存 Copy as cURL 的内容
python "%USERPROFILE%/.workbuddy/scripts/web_auth.py" --setup-har <文件>        # 从导出的 HAR 提取
python "%USERPROFILE%/.workbuddy/scripts/web_auth.py" --check                   # 只校验凭据（只读）
python "%USERPROFILE%/.workbuddy/scripts/web_auth.py" --forget                  # 清除本机保存的网页授权凭据

# --- 长效令牌（通道 C：保存一次后客户端不开也能签到，v3.1.7 新增）---
python "%USERPROFILE%/.workbuddy/scripts/rt_auth.py" --export-rt --save        # 客户端已登录时：自动导出并保存长效令牌（推荐）
python "%USERPROFILE%/.workbuddy/scripts/rt_auth.py" --setup-rt '<令牌>'        # 手动录入你已保管的令牌
python "%USERPROFILE%/.workbuddy/scripts/rt_auth.py"                           # 签到 + 派猫猫（自动续期）
python "%USERPROFILE%/.workbuddy/scripts/rt_auth.py" --check                   # 只读查询：签到状态/连签/余额/旅行状态
python "%USERPROFILE%/.workbuddy/scripts/rt_auth.py" --info                    # 查看本机令牌保存状态（脱敏）
python "%USERPROFILE%/.workbuddy/scripts/rt_auth.py" --forget                  # 清除本机保存的长效令牌

# --- 其他 ---
python "%USERPROFILE%/.workbuddy/scripts/auto_checkin.py" --no-notify           # 跳过推送与桌面通知（调试）
python "%USERPROFILE%/.workbuddy/scripts/workbuddy_checkin.py" --diagnose       # 环境自检（只读）
python "%USERPROFILE%/.workbuddy/scripts/workbuddy_checkin.py" --init-config    # 生成配置模板
python "%USERPROFILE%/.workbuddy/scripts/workbuddy_checkin.py" --version        # 显示版本
```

### 真实输出片段

`travel`（只读查状态）：

```json
{
  "status": "ok",
  "action": "travel",
  "msg": "派猫猫旅行：空闲（可派遣）",
  "travel": {
    "available": true,
    "state": "idle",
    "state_text": "空闲（可派遣）",
    "location_name": null,
    "reward_credit": 0,
    "daily_limit_reached": true,
    "locations": [
      { "id": 1, "name": "咖啡馆" },
      { "id": 2, "name": "商场店铺" },
      { "id": 3, "name": "健身房" },
      { "id": 4, "name": "古镇客栈" }
    ]
  }
}
```

`travel --travel-auto`（闭环；本次已达每日上限，故只记录跳过原因、未发写请求）：

```json
{
  "msg": "派猫猫旅行：空闲（可派遣），今日派遣次数已用完，跳过派遣",
  "travel": {
    "state": "idle",
    "daily_limit_reached": true,
    "auto_log": ["今日派遣次数已用完，跳过派遣"]
  }
}
```

`--check-only`（签到状态 + 旅行状态合并进同一条消息）：

```json
{
  "action": "skip_check_only",
  "msg": "状态查询成功（未执行领取） ｜ 派猫猫旅行：空闲（可派遣）"
}
```

`--diagnose`（只读自检，节选）：

```json
{
  "version": "3.1.7",
  "python": { "version": "3.13.14", "ok": true },
  "auth": { "found": true, "token_present": true, "domain": "www.codebuddy.cn", "expired": false },
  "network": { "dns_ok": true, "host": "www.codebuddy.cn" },
  "desktop": { "session": "yes", "note": "存在桌面会话，系统通知可正常弹出" },
  "notify_config": {
    "present": true,
    "enabled": true,
    "success_notify": true,
    "ready": ["wechat"],
    "unconfigured": ["dingtalk", "feishu", "wecom", "email", "sms", "qq", "slack", "telegram", "bark", "webhook"]
  }
}
```

推送（`push_message.py` 独立运行，节选；本机仅配了 PushPlus，故 `ready=[wechat]`）：

```json
// --channels system（零配置，离线可用）
{ "ok": true,
  "results": [{ "channel": "system", "status": "success",
                "detail": "通知中心文件:written; 桌面弹窗:已尝试(尽力)" }] }

// --channels dingtalk,system（未配置渠道标 unconfigured，其余照发）
{ "ok": false,
  "results": [
    { "channel": "dingtalk", "status": "unconfigured", "detail": "渠道 dingtalk 未配置" },
    { "channel": "system",   "status": "success", "detail": "通知中心文件:written; 桌面弹窗:已尝试(尽力)" }] }

// --channels sms（未配置 → unconfigured；若已配置但缺 --confirm-paid → skipped）
{ "ok": false,
  "results": [{ "channel": "sms", "status": "unconfigured", "detail": "渠道 sms 未配置" }] }
```

---

## 3. 消息推送配置（可选，12 渠道）

### 3.1 配置文件路径

`%USERPROFILE%\.workbuddy\scripts\notify_config.json`（Windows；macOS / Linux 为 `~/.workbuddy/scripts/notify_config.json`）。
也支持环境变量 `WORKBUDDY_CHECKIN_PUSH_CONFIG` 指向自定义路径。**文件不存在 → 完全不推送，行为与未引入推送功能时一致。**

### 3.2 写法一：新结构 `channels`（推荐，渠道最全）

```json
{
  "enabled": true,
  "success_notify": false,
  "channels": {
    "dingtalk": { "webhook": "https://oapi.dingtalk.com/robot/send?access_token=你的TOKEN", "secret": "加签密钥(可选)" },
    "feishu":   { "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/你的HOOK" },
    "wecom":    { "webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的KEY" },
    "wechat":   { "pushplus_token": "你的PushPlus_token" },
    "email": {
      "smtp_host": "smtp.qq.com", "smtp_port": 465, "smtp_mode": "ssl",
      "smtp_user": "你的邮箱@qq.com", "smtp_pass": "邮箱授权码",
      "from": "你的邮箱@qq.com", "to": "接收邮箱@qq.com"
    },
    "sms":      { "url": "https://你的短信服务/send", "payload_template": "{\"content\":\"{{content}}\"}" },
    "qq":       { "url": "https://你的QQ推送网关/send" },
    "slack":    { "webhook_url": "https://hooks.slack.com/services/你的WEBHOOK" },
    "telegram": { "bot_token": "你的BOT_TOKEN", "chat_id": "-100123456" },
    "bark":     { "server": "https://api.day.app", "device_key": "你的KEY" },
    "webhook":  { "url": "https://你的服务/hook", "payload_template": "{\"title\":\"{{title}}\",\"text\":\"{{content}}\"}" }
  }
}
```

### 3.3 写法二：旧结构扁平字段（向后兼容，仅微信三通道）

```json
{
  "enabled": true,
  "success_notify": false,
  "wecom_webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的群机器人KEY",
  "pushplus_token": "你的PushPlus_token",
  "bark_url": "https://api.day.app/你的KEY/"
}
```

两种写法**可混用**：`normalize_channels` 会先取新结构，再用旧字段补齐缺失的微信通道；`bark_url` 会自动拆成 `bark.server` + `bark.device_key`。

### 3.4 字段说明

| 字段 | 作用 |
|---|---|
| `enabled` | 总开关；`false` 时不推送（显式 `--push-channels` 可覆盖） |
| `success_notify` | `true` 时签到成功也推播报；默认 `false` 仅失败时提醒 |
| `channels` | 新结构渠道映射，按需只填你用得到的渠道 |
| `wecom_webhook` / `pushplus_token` / `bark_url` | 旧结构兼容字段，效果等同新结构里的 `wecom` / `wechat` / `bark` |

- 未配置的渠道**不会报错**，只在结果里标 `unconfigured`。
- 不想手写？运行 `python workbuddy_checkin.py --init-config` 生成双结构模板（也可直接看技能包内 `templates/notify_config.json.example`）。
- 密钥只用在本机，**不进脚本、不进本 Skill 目录**，可放心转发技能给他人。

### 3.5 如何获取密钥

| 渠道 | 获取方式 |
|------|----------|
| 钉钉群机器人 | 钉钉群 → 群设置 → 智能群助手 → 添加机器人 → 自定义，复制 Webhook；选「加签」时把密钥填到 `secret` |
| 飞书群机器人 | 飞书群 → 设置 → 群机器人 → 添加自定义机器人，复制 Webhook |
| 企业微信群机器人 | 企业微信群 → 群设置 → 群机器人 → 添加机器人，复制 Webhook URL |
| 微信（PushPlus） | 注册 https://www.pushplus.plus ，在「一对一推送」拿到 token |
| 邮件 | 用邮箱服务商的 SMTP 服务；`smtp_pass` 填**授权码**（非登录密码），QQ 邮箱在「设置 → 账户」开启 SMTP 后获取 |
| Bark（iOS） | 安装 Bark App，App 内复制 `https://api.day.app/<KEY>/` |
| Telegram | 找 @BotFather 建 bot 拿 `bot_token`；`chat_id` 可用 @userinfobot 查 |
| Slack | Slack App 里创建 Incoming Webhook，复制 URL |
| 通用 Webhook / 短信 / QQ | 用你自己的网关地址，配 `url` 与可选 `payload_template` |

### 3.6 调试推送

```bash
push_message.py --ready                              # 只列出已就绪/未配置渠道（只读，不发送）
push_message.py --title "测试" --content "hello"      # 发全部已配置渠道
push_message.py --title T --content C --channels system   # 只发系统通知（零配置，离线可测，不联网）
workbuddy_checkin.py --push-channels dingtalk,email  # 本次仅推这两个渠道
workbuddy_checkin.py --confirm-paid                  # 放行付费渠道（短信）
```

---

## 4. 安装与卸载

### 安装（用户级）

将本技能目录安装到 `~/.workbuddy/skills/totorosir-workbuddy-checkin/`（Windows 即 `%USERPROFILE%\.workbuddy\skills\totorosir-workbuddy-checkin\`）。
脚本落位时，`auto_checkin.py` / `workbuddy_checkin.py` / `web_auth.py` / `push_message.py` **四个文件都要**复制到 `~/.workbuddy/scripts/`（必须同目录：统一入口按同目录导入两条通道，两条通道按同目录导入推送模块；缺文件时对应能力自动降级，签到本身在通道 A 可用时不受影响）。

若目标机器走通道 B（本机登录态不可用），授权凭据保存在 `~/.workbuddy/checkin-cred.json`，与技能目录无关、不随技能包分发。

### 卸载 / 暂停

- **暂停每日自动签到**：在 WorkBuddy「自动化」列表把「WorkBuddy签到助手 · 每日自动签到」设为暂停（PAUSED）或删除，不影响脚本本身。
- **卸载本 Skill**：删除技能目录 `~/.workbuddy/skills/totorosir-workbuddy-checkin/`，必要时一并删除脚本副本 `~/.workbuddy/scripts/` 下的 `auto_checkin.py` / `workbuddy_checkin.py` / `web_auth.py` / `push_message.py`。
- **清除网页授权凭据**（做过通道 B 授权时）：`python ~/.workbuddy/scripts/web_auth.py --forget`，或直接删除 `~/.workbuddy/checkin-cred.json`；在网页端退出登录也会使其失效。
- **清理推送痕迹**（可选）：删除 `~/.workbuddy/scripts/notify_config.json`（含密钥）与 `~/.workbuddy/scripts/notifications.jsonl`（`system` 渠道的本地记录）。
- 彻底移除 = 暂停自动化 + 删除技能目录 + 删除脚本副本，无残留系统服务。

---

## 5. 反模式（不要这样做）

- ❌ 不要用网页版「签到入口」运行签到（网页版没有签到入口，签到是 PC 客户端专属）。通道 B 只是复用网页端**已登录的会话**调用同一组官方接口，不等于网页版可以签到。
- ❌ 不要手动把 `auth.domain` 改成 `copilot.tencent.com` 等域名（脚本自动以本机登录态为准，错误域名会 404）。
- ❌ 不要用签到域名调旅行接口，也不要给旅行路径加 `/v2`（旅行是 `www.workbuddy.cn` 且不带 `/v2`，写错一律 404）。
- ❌ 不要在 `daily_limit_reached` 为真时强行派遣（服务端每日限额，脚本已自动跳过，不要绕过）。
- ❌ 不要调用兑换 / 抽奖等未经验证的写接口（写操作只允许 `daily-checkin`、`travel/claim`、`travel/depart` 三个）。
- ❌ 不要把 `notify_config.json`（含 webhook/token）提交到任何仓库或分享给他人。
- ❌ 不要往付费渠道（`sms`）盲发，也不要自动加 `--confirm-paid`；付费渠道必须由用户显式确认。
- ❌ 不要让推送失败影响签到结论或退出码（脚本已做隔离，改造时不要破坏这个边界）。
- ❌ 不要给推送模块引入第三方 SDK / PyPI 依赖（保持纯标准库，避免用户侧装包失败）。
- ❌ 不要只复制主脚本、漏掉 `web_auth.py` / `push_message.py` / `auto_checkin.py`（四个脚本必须同目录：统一入口与推送模块都按同目录导入，缺文件时对应能力静默降级）。
- ❌ 不要在本机登录态可用时让用户去做网页授权（多余操作）；先用 `auto_checkin.py --probe` 判断。
- ❌ 不要在本机登录态不可用时反复重试或尝试寻找解密密钥（macOS 上属系统级限制，无解）——直接引导一次网页授权。
- ❌ 不要把网页授权凭据写进技能目录、随包分发或提交到仓库。
- ❌ 不要在汇报里回显任何推送凭据（webhook / smtp_pass / pushplus_token / bot_token）。
- ❌ 不要用 crontab / 系统计划任务替代 WorkBuddy 自带自动化（Skill 场景内）；系统级定时任务请用独立的「WorkBuddy 自动签到分享包」。
- ❌ 不要伪造、猜测或回显 token / accessToken / refreshToken。
- ❌ 不要无限重试（脚本内部最多重试 1 次）。
