#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy签到助手 · 多渠道消息推送模块（仅使用 Python 标准库）。

接口设计与配置项对齐 totorosir-push-message 技能，作为本签到技能的
自包含推送能力（不依赖任何外部 Skill，便于离线分发）。

支持渠道：dingtalk, feishu, wecom, wechat(pushplus), email, sms,
qq, slack, telegram, bark, webhook(通用), system(本地系统通知，零配置不过网)。

配置来源（沿用签到技能的 notify_config.json）：
  - 环境变量 WORKBUDDY_CHECKIN_PUSH_CONFIG
  - ~/.workbuddy/scripts/notify_config.json（默认）
同时兼容两种写法：
  (a) 新结构：{"channels": {"wecom": {"webhook": "..."}, ...}}
  (b) 旧结构（向后兼容）：{"wecom_webhook": "...", "pushplus_token": "...", "bark_url": "..."}

所有 HTTP 调用使用 urllib，邮件使用 smtplib，无第三方依赖。
凭据仅从本地配置文件读取，绝不输出到日志或 stdout 的值。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import smtplib
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
CONFIG_ENV = "WORKBUDDY_CHECKIN_PUSH_CONFIG"
DEFAULT_CONFIG = Path.home() / ".workbuddy" / "scripts" / "notify_config.json"
NOTIFY_DIR = DEFAULT_CONFIG.parent  # 系统通知中心文件目录
ZERO_CONFIG = {"system"}  # 无需任何凭据/配置的渠道

# 付费渠道：未获 confirm_paid 明确确认不发送
PAID_CHANNELS = {"sms"}

SUPPORTED = [
    "dingtalk", "feishu", "wecom", "wechat", "email",
    "sms", "qq", "slack", "telegram", "bark", "webhook", "system",
]


class ConfigError(Exception):
    """配置缺失或无法解析。"""


class ChannelError(Exception):
    """未知或不可用的渠道。"""


# --------------------------------------------------------------------------- #
# 配置加载与归一化
# --------------------------------------------------------------------------- #
def load_raw_config(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """读取原始 notify_config.json（可能含新结构或旧结构）。"""
    candidates: List[Path] = []
    if path:
        candidates.append(Path(path).expanduser())
    env = os.environ.get(CONFIG_ENV)
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(DEFAULT_CONFIG)
    for cand in candidates:
        if cand and cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ConfigError("配置文件无法解析 %s: %s" % (cand, exc))
            if not isinstance(data, dict):
                raise ConfigError("配置文件根节点必须是对象")
            return data
    return None


def normalize_channels(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """把原始配置归一化为内部 channel->credentials 映射，新结构优先、旧字段补齐缺口。

    - 新结构：取 cfg["channels"] 里的各渠道配置（同渠道以新结构为准）
    - 旧结构：把 wecom_webhook / pushplus_token / bark_url 映射成对应渠道
    - 两者**按渠道合并**：某渠道已在 channels 中出现则用新结构，否则用旧字段补齐。

    合并而非二选一，是为了避免老用户「只想新增一个渠道、结果原有微信通道静默失效」。
    """
    out: Dict[str, Dict[str, Any]] = {}
    channels = cfg.get("channels")
    if isinstance(channels, dict):
        for name, val in channels.items():
            if isinstance(val, dict):
                out[name] = val

    # 旧结构兼容：仅补齐新结构里没有的渠道
    if "wecom" not in out:
        wecom = cfg.get("wecom_webhook")
        if wecom:
            out["wecom"] = {"webhook": wecom}
    if "wechat" not in out:
        pushplus = cfg.get("pushplus_token")
        if pushplus:
            out["wechat"] = {"pushplus_token": pushplus}
    if "bark" not in out:
        bark = cfg.get("bark_url")
        if bark:
            server, key = _split_bark_url(bark)
            if server and key:
                out["bark"] = {"server": server, "device_key": key}
    return out


def _split_bark_url(bark_url: str) -> Tuple[str, str]:
    """bark_url 形如 https://api.day.app/<key>/ ，拆出 server 与 device_key。"""
    s = bark_url.strip().rstrip("/")
    if "/" not in s:
        return s, ""
    head, _, tail = s.rpartition("/")
    return head, tail


def resolve_channels(cfg: Dict[str, Any], requested: Optional[List[str]] = None) -> List[str]:
    """解析本次要发送的渠道列表。

    - requested 给定 → 使用它（已存在的才发，缺失标 unconfigured）
    - 否则用配置里存在的全部渠道
    """
    available = normalize_channels(cfg)
    if requested:
        return [c for c in requested if c in available] + \
               [c for c in requested if c not in available]
    return list(available.keys())


def channel_cfg(cfg: Dict[str, Any], channel: str) -> Dict[str, Any]:
    channels = normalize_channels(cfg)
    if channel not in channels:
        raise ConfigError("渠道 %s 未在配置中定义" % channel)
    return channels[channel]


# --------------------------------------------------------------------------- #
# 签名
# --------------------------------------------------------------------------- #
def hmac_sign(secret: str, timestamp: int) -> str:
    """钉钉 / 飞书 / 企业微信机器人加签。

    规则：base64(HMAC-SHA256(timestamp + "\\n" + secret))。
    钉钉、企业微信使用毫秒级时间戳；飞书使用秒级时间戳。
    """
    string_to_sign = "%d\n%s" % (timestamp, secret)
    mac = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("utf-8")


# --------------------------------------------------------------------------- #
# HTTP 发送
# --------------------------------------------------------------------------- #
def http_post(url: str, payload: Dict[str, Any],
              headers: Optional[Dict[str, str]] = None,
              timeout: int = 10) -> Tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        req_headers.update(headers)
    request = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        return 0, "网络错误: %s" % exc.reason


def _render(template: str, msg: Dict[str, Any]) -> str:
    """把模板里的 {{title}} / {{content}} 替换为消息内容。"""
    return template.replace("{{title}}", msg.get("title", "")).replace("{{content}}", msg.get("content", ""))


# --------------------------------------------------------------------------- #
# 各渠道构建：返回 ("http", url, payload, headers) 或 ("smtp", cfg, msg)
# --------------------------------------------------------------------------- #
def build_dingtalk(cfg: Dict[str, Any], msg: Dict[str, Any]):
    webhook = cfg.get("webhook")
    if not webhook:
        raise ConfigError("dingtalk 缺少 webhook")
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": msg.get("title", ""), "text": msg.get("content", "")},
    }
    url = webhook
    secret = cfg.get("secret")
    if secret:
        ts = int(time.time() * 1000)
        url = "%s&timestamp=%d&sign=%s" % (webhook, ts, hmac_sign(secret, ts))
    return ("http", url, payload, None)


def build_feishu(cfg: Dict[str, Any], msg: Dict[str, Any]):
    webhook = cfg.get("webhook")
    if not webhook:
        raise ConfigError("feishu 缺少 webhook")
    payload = {"msg_type": "text", "content": {"text": "%s\n%s" % (msg.get("title", ""), msg.get("content", ""))}}
    url = webhook
    secret = cfg.get("secret")
    if secret:
        ts = int(time.time())
        sign = hmac_sign(secret, ts)
        sep = "&" if "?" in url else "?"
        url = "%s%stimestamp=%d&sign=%s" % (url, sep, ts, sign)
    return ("http", url, payload, None)


def build_wecom(cfg: Dict[str, Any], msg: Dict[str, Any]):
    webhook = cfg.get("webhook")
    if not webhook:
        raise ConfigError("wecom 缺少 webhook")
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": "# %s\n%s" % (msg.get("title", ""), msg.get("content", ""))},
    }
    url = webhook
    secret = cfg.get("secret")
    if secret:
        ts = int(time.time() * 1000)
        url = "%s&timestamp=%d&sign=%s" % (webhook, ts, hmac_sign(secret, ts))
    return ("http", url, payload, None)


def build_wechat(cfg: Dict[str, Any], msg: Dict[str, Any]):
    token = cfg.get("pushplus_token")
    if not token:
        raise ConfigError("wechat 缺少 pushplus_token")
    url = "https://www.pushplus.plus/send"
    template = "markdown" if msg.get("content_type") == "markdown" else "html"
    payload = {
        "token": token,
        "title": msg.get("title", ""),
        "content": msg.get("content", ""),
        "template": template,
    }
    return ("http", url, payload, None)


def build_email(cfg: Dict[str, Any], msg: Dict[str, Any]):
    for key in ("smtp_host", "smtp_user", "smtp_pass", "from", "to"):
        if not cfg.get(key):
            raise ConfigError("email 缺少 %s" % key)
    return ("smtp", cfg, msg)


def build_sms(cfg: Dict[str, Any], msg: Dict[str, Any]):
    url = cfg.get("url")
    if not url:
        raise ConfigError("sms 缺少 url")
    template = cfg.get("payload_template")
    if template:
        payload = json.loads(_render(template, msg))
    else:
        payload = {"content": msg.get("content", "")}
    headers = cfg.get("headers")
    return ("http", url, payload, headers)


def build_qq(cfg: Dict[str, Any], msg: Dict[str, Any]):
    # QQ 通过通用 webhook 网关推送
    return build_webhook(cfg, msg)


def build_slack(cfg: Dict[str, Any], msg: Dict[str, Any]):
    url = cfg.get("webhook_url")
    if not url:
        raise ConfigError("slack 缺少 webhook_url")
    payload = {"text": "*%s*\n%s" % (msg.get("title", ""), msg.get("content", ""))}
    return ("http", url, payload, None)


def build_telegram(cfg: Dict[str, Any], msg: Dict[str, Any]):
    token = cfg.get("bot_token")
    chat_id = cfg.get("chat_id")
    if not token or not chat_id:
        raise ConfigError("telegram 缺少 bot_token 或 chat_id")
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    text = "%s\n%s" % (msg.get("title", ""), msg.get("content", ""))
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    return ("http", url, payload, None)


def build_bark(cfg: Dict[str, Any], msg: Dict[str, Any]):
    server = cfg.get("server")
    key = cfg.get("device_key")
    if not server or not key:
        raise ConfigError("bark 缺少 server 或 device_key")
    url = "%s/push" % server.rstrip("/")
    payload = {"device_key": key, "title": msg.get("title", ""), "body": msg.get("content", "")}
    return ("http", url, payload, None)


def build_webhook(cfg: Dict[str, Any], msg: Dict[str, Any]):
    url = cfg.get("url")
    if not url:
        raise ConfigError("webhook 缺少 url")
    template = cfg.get("payload_template")
    if template:
        payload = json.loads(_render(template, msg))
    else:
        payload = {"title": msg.get("title", ""), "content": msg.get("content", "")}
    headers = cfg.get("headers")
    return ("http", url, payload, headers)


CHANNELS = {
    "dingtalk": build_dingtalk,
    "feishu": build_feishu,
    "wecom": build_wecom,
    "wechat": build_wechat,
    "email": build_email,
    "sms": build_sms,
    "qq": build_qq,
    "slack": build_slack,
    "telegram": build_telegram,
    "bark": build_bark,
    "webhook": build_webhook,
}


# --------------------------------------------------------------------------- #
# 邮件发送
# --------------------------------------------------------------------------- #
def send_email(cfg: Dict[str, Any], msg: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    host = cfg["smtp_host"]
    port = int(cfg.get("smtp_port", 465))
    mode = cfg.get("smtp_mode", "ssl")
    subtype = "html" if msg.get("content_type") == "html" else "plain"
    mime = MIMEMultipart()
    mime["From"] = cfg["from"]
    mime["To"] = cfg["to"]
    mime["Subject"] = msg.get("title", "")
    mime.attach(MIMEText(msg.get("content", ""), subtype, "utf-8"))
    if mode == "starttls":
        with smtplib.SMTP(host, port, timeout=timeout) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(cfg["smtp_user"], cfg["smtp_pass"])
            server.sendmail(cfg["from"], cfg["to"], mime.as_string())
    else:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=timeout) as server:
            server.login(cfg["smtp_user"], cfg["smtp_pass"])
            server.sendmail(cfg["from"], cfg["to"], mime.as_string())
    return {"channel": "email", "status": "success", "http_code": 250, "detail": "已投递"}


# --------------------------------------------------------------------------- #
# 单渠道发送
# --------------------------------------------------------------------------- #
def desktop_toast(title: str, content: str) -> None:
    """尽力弹出桌面通知；任何失败静默忽略，不阻塞发送流程（守护线程）。"""
    text = "%s\n%s" % (title, content)

    def _run() -> None:
        try:
            if sys.platform.startswith("win"):
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
            elif sys.platform == "darwin":
                # AppleScript 字符串需转义反斜杠与双引号，避免内容里的引号破坏脚本
                def _esc(s: str) -> str:
                    return s.replace("\\", "\\\\").replace('"', '\\"')
                subprocess.run(
                    ["osascript", "-e",
                     'display notification "%s" with title "%s"' % (_esc(content), _esc(title))],
                    check=False, timeout=5,
                )
            elif sys.platform.startswith("linux"):
                # 列表形式传参，不经过 shell，天然免疫注入
                subprocess.run(["notify-send", title, content], check=False, timeout=5)
        except Exception:  # noqa: BLE001 - 弹窗只是加分项，失败不抛
            pass

    threading.Thread(target=_run, daemon=True).start()


def send_system(msg: Dict[str, Any], timeout: int = 10) -> Dict[str, Any]:
    """系统通知：写入本地通知中心文件，并尽力弹窗。零配置、不过网。"""
    NOTIFY_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": int(time.time()),
        "title": msg.get("title", ""),
        "content": msg.get("content", ""),
        "content_type": msg.get("content_type", "text"),
    }
    path = NOTIFY_DIR / "notifications.jsonl"
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        file_status = "written"
    except OSError as exc:
        file_status = "write_failed:%s" % exc
    desktop_toast(msg.get("title", ""), msg.get("content", ""))
    ok = file_status == "written"
    return {
        "channel": "system",
        "status": "success" if ok else "failed",
        "detail": "通知中心文件:%s; 桌面弹窗:已尝试(尽力)" % file_status,
        "file": str(path),
    }


def send_one(channel: str, cfg: Dict[str, Any], msg: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    if channel == "system":
        return send_system(msg, timeout)
    if channel not in CHANNELS:
        return {"channel": channel, "status": "failed", "detail": "未知渠道 %s" % channel}
    builder = CHANNELS[channel]
    try:
        built = builder(cfg, msg)
    except ConfigError as exc:
        return {"channel": channel, "status": "unconfigured", "detail": str(exc)}
    if built[0] == "smtp":
        try:
            return send_email(built[1], built[2], timeout)
        except Exception as exc:  # noqa: BLE001
            return {"channel": channel, "status": "failed", "detail": str(exc)[:500]}
    _, url, payload, headers = built
    try:
        code, detail = http_post(url, payload, headers, timeout)
    except Exception as exc:  # noqa: BLE001
        return {"channel": channel, "status": "failed", "detail": str(exc)[:500]}
    ok = 200 <= code < 300
    return {
        "channel": channel,
        "status": "success" if ok else "failed",
        "http_code": code,
        "detail": detail[:500],
    }


# --------------------------------------------------------------------------- #
# 统一发送入口
# --------------------------------------------------------------------------- #
def send_message(title: str, content: str, channels: Optional[List[str]] = None,
                 content_type: str = "markdown", confirm_paid: bool = False,
                 timeout: int = 10, config_path: Optional[str] = None) -> Dict[str, Any]:
    """向指定渠道发送一条通知，返回聚合结果。

    channels=None 时，发送配置里已就绪的全部渠道；显式给定时只发指定的，
    未配置的标 unconfigured 不中断。付费渠道（sms）需 confirm_paid=True 才发。
    """
    raw = load_raw_config(config_path)
    if raw is None:
        return {"ok": True, "skipped": True,
                "results": [{"channel": "<none>", "status": "skipped", "detail": "未找到 notify_config.json"}]}

    # 顶层 enabled 开关
    if raw.get("enabled") is False and not channels:
        return {"ok": True, "skipped": True,
                "results": [{"channel": "<none>", "status": "skipped", "detail": "enabled=false，已静默"}]}

    requested = [c.strip() for c in (channels or []) if c.strip()]
    full = resolve_channels(raw, requested if requested else None)
    # 区分「已配置」与「未配置」；零配置渠道（system）无需配置即可发送
    configured = normalize_channels(raw)
    targets = []
    results: List[Dict[str, Any]] = []
    for ch in full:
        if ch in configured or ch in ZERO_CONFIG:
            targets.append(ch)
        elif ch in requested:
            results.append({"channel": ch, "status": "unconfigured", "detail": "渠道 %s 未配置" % ch})

    msg = {"title": title, "content": content, "content_type": content_type}

    paid_in_targets = [c for c in targets if c in PAID_CHANNELS]
    if paid_in_targets and not confirm_paid:
        for c in paid_in_targets:
            results.append({"channel": c, "status": "skipped", "detail": "付费渠道，未确认不发送"})
        targets = [c for c in targets if c not in PAID_CHANNELS]

    for ch in targets:
        try:
            results.append(send_one(ch, raw, msg, timeout))
        except ConfigError as exc:
            results.append({"channel": ch, "status": "unconfigured", "detail": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 单渠道失败不影响整体
            results.append({"channel": ch, "status": "failed", "detail": str(exc)[:500]})

    if not results:
        results = [{"channel": "<none>", "status": "skipped", "detail": "未配置任何渠道"}]
    ok_all = all(r["status"] == "success" for r in results)
    return {"ok": ok_all, "results": results}


def ready_channels(config_path: Optional[str] = None) -> Dict[str, Any]:
    """列出配置里已就绪 / 缺失的渠道，供 --diagnose 使用（只读，不联网）。"""
    raw = load_raw_config(config_path)
    if raw is None:
        return {"present": False, "hint": "未找到 notify_config.json"}
    configured = normalize_channels(raw)
    missing = []
    for name in SUPPORTED:
        if name in ZERO_CONFIG:
            continue
        if name not in configured:
            missing.append(name)
    return {
        "present": True,
        "enabled": raw.get("enabled", True),
        "success_notify": bool(raw.get("success_notify")),
        "ready": sorted(configured.keys()),
        "unconfigured": [m for m in missing if m not in configured],
    }


# --------------------------------------------------------------------------- #
# 配置模板（新结构 channels；旧扁平字段仍被识别，见 normalize_channels）
# --------------------------------------------------------------------------- #
_SAMPLE_CONFIG = {
    "_说明": "WorkBuddy签到助手推送配置模板。按需只保留你用得到的渠道，未配置的渠道会自动跳过（不报错）。凭据仅存本地，勿提交到 git 或外发。",
    "_旧结构写法": "若你沿用 2.x 的扁平字段（wecom_webhook / pushplus_token / bark_url），无需改动，脚本会自动识别并与本结构按渠道合并；同渠道以本结构为准。详见 README『配置消息推送』。",
    "enabled": True,
    "success_notify": False,
    "channels": {
        "dingtalk": {"webhook": "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN", "secret": "YOUR_SECRET(可选加签)"},
        "feishu": {"webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_HOOK", "secret": "YOUR_SECRET(可选)"},
        "wecom": {"webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY", "secret": "YOUR_SECRET(可选)"},
        "wechat": {"pushplus_token": "YOUR_PUSHPLUS_TOKEN（微信个人号中转）"},
        "email": {"smtp_host": "smtp.qq.com", "smtp_port": 465, "smtp_mode": "ssl", "smtp_user": "123@qq.com", "smtp_pass": "邮箱授权码(非登录密码)", "from": "123@qq.com", "to": "to@qq.com"},
        "sms": {"url": "https://your-sms-provider.com/send", "payload_template": '{"content":"{{content}}"}', "headers": {"Authorization": "Bearer YOUR_KEY"}},
        "qq": {"url": "https://your-qq-push-gateway/send"},
        "slack": {"webhook_url": "https://hooks.slack.com/services/YOUR/WEBHOOK"},
        "telegram": {"bot_token": "YOUR_BOT_TOKEN", "chat_id": "-100123456"},
        "bark": {"server": "https://api.day.app", "device_key": "YOUR_DEVICE_KEY"},
        "webhook": {"url": "https://your-endpoint/hook", "payload_template": '{"title":"{{title}}","text":"{{content}}"}'},
        "system": {},
    },
}


def write_sample(config_path: Optional[str] = None) -> str:
    target = Path(config_path).expanduser() if config_path else (DEFAULT_CONFIG.parent / "notify_config.json.example")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(_SAMPLE_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target)


# --------------------------------------------------------------------------- #
# 入口（可独立运行，便于单独测试推送）
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="WorkBuddy签到助手 · 多渠道发送器")
    parser.add_argument("--title", required=True, help="通知标题")
    parser.add_argument("--content", required=True, help="通知正文")
    parser.add_argument("--channels", help="逗号分隔的渠道列表，如 dingtalk,email；缺省发全部已配置渠道")
    parser.add_argument("--content-type", default="markdown", choices=["markdown", "text", "html"])
    parser.add_argument("--confirm-paid", action="store_true", help="确认发送付费渠道（短信）")
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--ready", action="store_true", help="仅列出已就绪渠道（只读）")
    args = parser.parse_args()

    if args.ready:
        print(json.dumps(ready_channels(args.config), ensure_ascii=False, indent=2))
        return 0

    chs = [c.strip() for c in args.channels.split(",") if c.strip()] if args.channels else None
    result = send_message(args.title, args.content, channels=chs,
                          content_type=args.content_type, confirm_paid=args.confirm_paid,
                          timeout=args.timeout, config_path=args.config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
