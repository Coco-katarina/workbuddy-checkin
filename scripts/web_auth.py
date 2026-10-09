#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy签到助手 · 网页会话授权通道（v3.1.7）

适用场景：本机登录态无法在本地自动解密时（典型为 macOS 标准安装），
改用「用户本人一次性授权」完成「Buddy 加油站」签到与派猫猫旅行。

凭据来源（三选一，均由用户主动提供，脚本不自行查找或提取）：
  1) 网页授权码（推荐）：在已登录的浏览器里运行 templates/auth-snippet.js，
     得到以 WBAUTH1: 开头的授权码，粘贴给本脚本。授权码自包含会话凭据、
     原始 User-Agent 与生效域名，因此不受官方网关「会话与 UA 绑定」影响。
  2) 开发者工具复制的 cURL：请求右键 -> Copy as cURL，整段粘贴。
  3) 用户主动导出的 HAR 文件。

凭据仅存放于本机 ~/.workbuddy/checkin-cred.json（POSIX 下 0600）；
脚本只读这一个凭据文件，不读取客户端数据、不扫描进程、不做加解密。

命令行：
  --setup-code <code>    保存网页授权码（'-' 表示从标准输入读取）
  --setup-curl <text>    保存从开发者工具复制的 cURL（含 Cookie 与 UA）
  --setup-har <path>     从用户导出的 HAR 文件提取凭据
  --setup-file <path>    从文本文件读取授权码 / cURL（避免进入命令历史）
  --snippet              打印网页授权码工具与用法
  --check                只校验凭据与查询状态（不领取、不派遣）
  --no-travel            跳过派猫猫旅行
  --no-notify            跳过消息推送与桌面通知
  --location <1-4>       指定旅行地点（缺省随机，四地收益相同）
  --domain <url>         覆盖签到域（缺省自动在官方双域间探测）
  --travel-domain <url>  覆盖旅行域（缺省 https://www.workbuddy.cn）
  --version              打印版本
  --help                 打印本帮助
"""

import base64
import binascii
import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.request

VERSION = "3.1.7"
HTTP_TIMEOUT = 12

HOME = os.path.expanduser("~")
WORKBUDDY_DIR = os.path.join(HOME, ".workbuddy")
# 唯一读取的凭据文件（本通道专用）
CRED_FILE = os.path.join(WORKBUDDY_DIR, "checkin-cred.json")
# 用户本地推送配置（与主脚本共用同一份）
NOTIFY_CONFIG = os.path.join(WORKBUDDY_DIR, "scripts", "notify_config.json")
# 运行日志（与主脚本同目录、同格式，便于定时任务事后核查）
LOG_NAME = "checkin.log"

# 网页授权码前缀
AUTH_CODE_PREFIX = "WBAUTH1:"

# 官方域名（自动探测，命中即用）
CHECKIN_DOMAINS = ["https://www.workbuddy.cn", "https://www.codebuddy.cn"]
TRAVEL_DOMAIN_DEFAULT = "https://www.workbuddy.cn"
LOGIN_URL = "https://www.workbuddy.cn"

# 官方网关把会话与 User-Agent 绑定：必须回放创建会话时的那个 UA，否则 401。
# 授权码 / cURL / HAR 都会带上原始 UA；仅当用户只粘贴裸凭据时才用它兜底。
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) "
              "Gecko/20100101 Firefox/156.0")

STATUS_PATH = "/v2/billing/meter/checkin-activity-status"
CHECKIN_PATH = "/v2/billing/meter/daily-checkin"
TRAVEL_STATUS_PATH = "/activity/growth/buddy/travel/status"
TRAVEL_CLAIM_PATH = "/activity/growth/buddy/travel/claim"
TRAVEL_DEPART_PATH = "/activity/growth/buddy/travel/depart"

# 首次授权引导（用户自行操作，脚本不读取任何浏览器数据）
SETUP_GUIDE = (
    "需要一次性授权后才能自动签到。三种方式任选其一（约 30 秒）：\n"
    "  【方式1 · 推荐】网页授权码：\n"
    "    1. 浏览器打开 %s 并登录（与客户端同一账号）\n"
    "    2. 按 F12 打开开发者工具，切到 Console，粘贴运行 auth-snippet.js 的内容\n"
    "    3. 授权码已复制到剪贴板，回来粘贴给助手即可\n"
    "    （运行 `--snippet` 可直接拿到该工具与完整说明）\n"
    "  【方式2】开发者工具复制 cURL：\n"
    "    1. F12 -> Network -> 刷新页面 -> 找到任意一个接口请求\n"
    "    2. 右键 -> Copy -> Copy as cURL，整段粘贴给助手\n"
    "    （适合会话 Cookie 为 HttpOnly、页面里读不到令牌的情况）\n"
    "  【方式3】导出 HAR：F12 -> Network -> 右键「保存所有为 HAR」-> "
    "`--setup-har <文件>`\n"
) % LOGIN_URL


# ----------------------------------------------------------------------------
# 基础编解码
# ----------------------------------------------------------------------------
def _b64url_encode(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text):
    """宽松解码：先去掉全部 padding 再按需补齐，容忍标准/URL 两种字符集。"""
    s = re.sub(r"\s+", "", text or "").rstrip("=")
    s = s.replace("+", "-").replace("/", "_")
    pad = (-len(s)) % 4
    return base64.urlsafe_b64decode(s + "=" * pad)


def _mask(val):
    if not val:
        return "<无凭据>"
    if len(val) > 16:
        return val[:6] + "..." + val[-4:]
    return "****"


def describe(cred):
    """按模式脱敏展示凭据，绝不输出明文。"""
    if not cred:
        return "<无凭据>"
    mode = cred.get("mode")
    if mode == "cookie":
        return "cookie(%d 字符)" % len(cred.get("value", ""))
    return "bearer:" + _mask(cred.get("value"))


def _origin_of(url):
    m = re.match(r"^(https?://[^/]+)", (url or "").strip())
    return m.group(1) if m else ""


# ----------------------------------------------------------------------------
# 凭据解析：授权码 / cURL / HAR / 裸凭据
# ----------------------------------------------------------------------------
def parse_auth_code(text):
    """解析 WBAUTH1: 授权码。返回原始字段 dict（v / mode / value / ua / domain）。"""
    s = (text or "").strip()
    idx = s.find(AUTH_CODE_PREFIX)
    if idx < 0:
        return None
    raw = s[idx + len(AUTH_CODE_PREFIX):].strip()
    raw = raw.split()[0] if raw and not raw.startswith("{") else raw
    try:
        obj = json.loads(_b64url_decode(raw).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(obj, dict) or not obj.get("value"):
        return None
    return obj


def _parts_from_code(obj):
    """把授权码字段转成统一的 parts 结构（供候选构造复用）。"""
    mode = (obj.get("mode") or "").strip().lower()
    val = (obj.get("value") or "").strip()
    if not val:
        return None
    parts = {"ua": obj.get("ua") or None, "cookie": None,
             "authorization": None, "url": obj.get("domain") or None}
    if mode == "cookie":
        parts["cookie"] = val
    elif val.lower().startswith("bearer "):
        parts["authorization"] = val
    else:
        parts["authorization"] = "Bearer " + val
    return parts


def _split_shell_tokens(text):
    """按 shell 引号规则切词；容忍 cURL 的换行续行（反斜杠 + 换行）。"""
    tokens = []
    cur = []
    quote = None
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"' and i + 1 < n:
                i += 1
                cur.append(text[i])
            else:
                cur.append(ch)
        else:
            if ch in ("'", '"'):
                quote = ch
            elif ch == "\\":
                if i + 1 < n:
                    nxt = text[i + 1]
                    if nxt in ("\n", "\r"):
                        i += 1
                        if nxt == "\r" and i + 1 < n and text[i + 1] == "\n":
                            i += 1
                    else:
                        i += 1
                        cur.append(text[i])
            elif ch.isspace():
                if cur:
                    tokens.append("".join(cur))
                    cur = []
            else:
                cur.append(ch)
        i += 1
    if cur:
        tokens.append("".join(cur))
    return tokens


def _absorb_header(parts, header):
    name, _, val = (header or "").partition(":")
    nm = name.strip().lower()
    val = val.strip()
    if not nm:
        return
    if nm == "user-agent":
        parts["ua"] = val
    elif nm == "cookie":
        parts["cookie"] = val
    elif nm == "authorization":
        parts["authorization"] = val


def parse_curl(text):
    """解析开发者工具「Copy as cURL」的整段命令。返回 parts dict 或 None。"""
    tokens = _split_shell_tokens(text or "")
    if not tokens:
        return None
    parts = {"ua": None, "cookie": None, "authorization": None, "url": None}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower()
        if low in ("-h", "--header"):
            i += 1
            if i < len(tokens):
                _absorb_header(parts, tokens[i])
        elif low.startswith("--header="):
            _absorb_header(parts, tok.split("=", 1)[1])
        elif low in ("-b", "--cookie"):
            i += 1
            if i < len(tokens):
                parts["cookie"] = tokens[i].strip()
        elif low.startswith("--cookie="):
            parts["cookie"] = tok.split("=", 1)[1].strip()
        elif low == "--url":
            i += 1
            if i < len(tokens):
                parts["url"] = tokens[i].strip()
        elif low.startswith("--url="):
            parts["url"] = tok.split("=", 1)[1].strip()
        elif low.startswith("http") and not parts["url"]:
            parts["url"] = tok
        i += 1
    if not (parts["cookie"] or parts["authorization"]):
        return None
    return parts


def parse_bookmarklet_output(text):
    """兼容早期书签工具的 COOKIE:... / LOCAL:{...} 输出格式。"""
    s = text or ""
    if "COOKIE:" not in s:
        return None
    parts = {"ua": None, "cookie": None, "authorization": None, "url": None}
    m = re.search(r"COOKIE:(.*?)(?:\nLOCAL:|$)", s, re.S)
    if m:
        parts["cookie"] = m.group(1).strip()
    m2 = re.search(r"LOCAL:(\{.*\})", s, re.S)
    if m2:
        try:
            obj = json.loads(m2.group(1))
        except Exception:
            obj = {}
        for k, v in (obj or {}).items():
            if isinstance(v, str) and re.search(r"^(eyJ|Bearer\s)", v.strip(), re.I):
                parts["authorization"] = v.strip()
                break
    if not (parts["cookie"] or parts["authorization"]):
        return None
    return parts


def parse_raw_credential(text):
    """裸凭据：Cookie 串或 Bearer 令牌（无 UA 信息，用默认 UA 兜底）。"""
    s = (text or "").strip()
    if not s:
        return None
    low = s.lower()
    if low.startswith("bearer "):
        return {"ua": None, "cookie": None,
                "authorization": s, "url": None}
    if low.startswith("eyj") or ("." in s and "=" not in s and ";" not in s):
        return {"ua": None, "cookie": None,
                "authorization": "Bearer " + s, "url": None}
    if "=" in s:
        return {"ua": None, "cookie": s, "authorization": None, "url": None}
    return None


def _har_header(entry, name):
    for h in (entry.get("request", {}).get("headers") or []):
        if str(h.get("name", "")).lower() == name:
            return str(h.get("value", ""))
    return ""


def parse_har(path):
    """从用户主动导出的 HAR 提取凭据。"""
    with open(path, "r", encoding="utf-8") as f:
        har = json.load(f)
    entries = har.get("log", {}).get("entries", []) or []
    bearer = None
    best = None  # (cookie, ua)
    for e in entries:
        auth = _har_header(e, "authorization")
        if auth and auth.strip().lower().startswith("bearer ") and not bearer:
            bearer = (auth.strip(), _har_header(e, "user-agent") or DEFAULT_UA)
        url = e.get("request", {}).get("url", "")
        if not any(d in url for d in ("workbuddy.cn", "codebuddy.cn")):
            continue
        if e.get("response", {}).get("status") != 200:
            continue
        ck = _har_header(e, "cookie")
        if ck and "session=" in ck:
            if best is None or len(ck) > len(best[0]):
                best = (ck, _har_header(e, "user-agent") or DEFAULT_UA)
    parts = {"ua": None, "cookie": None, "authorization": None,
             "url": _origin_of(entries[0].get("request", {}).get("url", ""))
             if entries else None}
    if best:
        parts["cookie"], parts["ua"] = best
    elif bearer:
        parts["authorization"], parts["ua"] = bearer
    else:
        return None
    return parts


def parse_any(text):
    """统一入口：依次尝试授权码 / cURL / 早期书签输出 / 裸凭据。

    返回 (parts, 解析器名)；parts 统一为 {ua, cookie, authorization, url} 结构。
    """
    try:
        obj = parse_auth_code(text)
    except Exception:
        obj = None
    if obj:
        parts = _parts_from_code(obj)
        if parts:
            return parts, "parse_auth_code"
    for fn in (parse_curl, parse_bookmarklet_output, parse_raw_credential):
        try:
            got = fn(text)
        except Exception:
            got = None
        if got:
            return got, fn.__name__
    return None, None


def build_candidates(parts, source):
    """把解析结果转成候选凭据列表（cookie 优先，其次 bearer）。

    返回 [(cred_dict, label), ...]；调用方按顺序校验，首个通过者落盘。
    """
    ua = parts.get("ua") or DEFAULT_UA
    dom = _origin_of(parts.get("url") or "")
    out = []
    if parts.get("cookie"):
        out.append(({"mode": "cookie", "value": parts["cookie"].strip(),
                     "user_agent": ua, "domain": dom, "source": source},
                    "cookie"))
    auth = (parts.get("authorization") or "").strip()
    if auth:
        val = re.sub(r"^bearer\s+", "", auth, flags=re.I).strip()
        if val:
            out.append(({"mode": "bearer", "value": val,
                         "user_agent": ua, "domain": dom, "source": source},
                        "bearer"))
    return out


# ----------------------------------------------------------------------------
# 凭据读写
# ----------------------------------------------------------------------------
def read_cred():
    """读取本通道凭据文件；不存在或损坏返回 None。"""
    try:
        if not os.path.isfile(CRED_FILE):
            return None
        with open(CRED_FILE, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict) and obj.get("value"):
            if not obj.get("mode"):
                obj["mode"] = "cookie" if "=" in obj["value"] else "bearer"
            return obj
    except Exception:
        return None
    return None


def save_cred(cred):
    """落盘凭据（仅本机，POSIX 下 0600）。"""
    os.makedirs(WORKBUDDY_DIR, exist_ok=True)
    obj = {
        "mode": cred.get("mode"),
        "value": cred.get("value"),
        "user_agent": cred.get("user_agent") or DEFAULT_UA,
        "checkin_domain": cred.get("domain") or "",
        "source": cred.get("source") or "unknown",
        "saved_at": int(time.time()),
        "version": VERSION,
    }
    with open(CRED_FILE, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(CRED_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    return obj


def delete_cred():
    """清除本通道凭据：把文件内容置空（保留空文件，不删除文件本身）。"""
    try:
        if os.path.isfile(CRED_FILE):
            with open(CRED_FILE, "w", encoding="utf-8") as f:
                f.write("{}")
            return True
    except Exception:
        pass
    return False


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
def _headers(cred, base):
    b = (base or "").rstrip("/")
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        "X-Client-Platform": "web",
        "Origin": b,
        "Referer": b + "/",
        "User-Agent": (cred or {}).get("user_agent") or DEFAULT_UA,
    }
    mode = (cred or {}).get("mode")
    val = (cred or {}).get("value", "")
    if mode == "cookie":
        h["Cookie"] = val
    elif mode == "bearer":
        h["Authorization"] = "Bearer " + val
    return h


def _safe_json(text):
    try:
        return json.loads(text)
    except Exception:
        return {"_raw": str(text)[:500]}


def request(method, path, cred, base, body=None):
    """发起一次请求。返回 (status, json_or_none)。不重试、不抛栈。"""
    url = (base or "").rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers=_headers(cred, base), method=method)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status, _safe_json(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            raw = ""
        return e.code, _safe_json(raw)
    except Exception as e:
        return 0, {"_error": str(e)}


def post(path, cred, body=None, base=None):
    return request("POST", path, cred, base, body if body is not None else {})


def get(path, cred, base):
    return request("GET", path, cred, base, None)


def probe_domains(cred):
    """按「凭据自带域名 → 官方双域」顺序探测可用签到域。"""
    cands = []
    for d in [cred.get("domain")] + CHECKIN_DOMAINS:
        d = (d or "").rstrip("/")
        if d and d not in cands:
            cands.append(d)
    return cands


def query_status(cred, domains=None):
    """查询签到状态（只读）。返回 (http, body, base)。"""
    last = (0, {"_error": "无可用域名"}, (domains or CHECKIN_DOMAINS)[0])
    for base in (domains or probe_domains(cred)):
        st, body = post(STATUS_PATH, cred, {}, base)
        if st == 200 and (body or {}).get("code") == 0:
            return st, body, base
        last = (st, body, base)
    return last


def validate_cred(cred):
    """校验候选凭据是否可用。返回 (ok, base, body)。"""
    st, body, base = query_status(cred)
    return (st == 200 and (body or {}).get("code") == 0), base, body


# ----------------------------------------------------------------------------
# 推送（复用与主脚本同一份 push_message.py，行为对齐）
# ----------------------------------------------------------------------------
def _import_push():
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import push_message
        return push_message
    except Exception:
        return None


def _load_notify_cfg():
    try:
        if os.path.isfile(NOTIFY_CONFIG):
            with open(NOTIFY_CONFIG, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else None
    except Exception:
        return None
    return None


def notify_result(res, suppress=False):
    """按本地配置推送结果并弹桌面通知；任何异常静默，绝不改变退出码。"""
    if suppress:
        return
    detail = res.setdefault("detail", {})
    now = time.strftime("%Y-%m-%d %H:%M:%S")  # 本机时区（北京时间）
    status = res.get("status")
    action = res.get("action")
    msg = res.get("msg", "")
    title = None
    content = None

    if status != "ok":
        title = "⚠️ WorkBuddy签到助手 · 签到失败"
        content = ("### ⚠️ WorkBuddy签到助手 · 签到失败\n\n"
                   "> **时间**：%s\n\n"
                   "> **原因**：%s\n\n"
                   "> **授权方式**：网页会话授权（%s）\n\n"
                   "> **处理建议**：凭据可能已过期，请重新授权一次；"
                   "或改用主脚本的本地登录态通道。\n"
                   % (now, msg, describe(res.get("cred"))))
    elif action in ("clicked", "skip_already_signed"):
        cfg = _load_notify_cfg()
        if not cfg or cfg.get("enabled") is False or not cfg.get("success_notify"):
            return
        if action == "skip_already_signed":
            title = "✅ WorkBuddy签到助手 · 今日已签"
            content = ("### ✅ WorkBuddy签到助手 · 今日已签\n\n"
                       "> **时间**：%s\n\n"
                       "> **状态**：今日已签到，无需重复领取（幂等保护）\n\n"
                       "> **说明**：系统定时任务 / 技能已正常执行，无需处理。\n"
                       % now)
        else:
            title = "✅ WorkBuddy签到助手 · 签到成功"
            content = ("### ✅ WorkBuddy签到助手 · 签到成功\n\n"
                       "> **时间**：%s\n\n"
                       "> **状态**：%s\n" % (now, msg))
            if res.get("points"):
                content += "> **积分**：+%s\n\n" % res["points"]
            streak = detail.get("streak_days")
            if streak:
                content += "> **连续天数**：第 %s 天\n\n" % streak
            if res.get("balance") is not None:
                content += "> **当前积分余额**：%s\n\n" % res["balance"]
            content += "> **说明**：系统定时任务 / 技能已正常执行，无需处理。\n"

    if not title:
        return
    pm = _import_push()
    if pm is None:
        return
    try:
        out = pm.send_message(title, content, content_type="markdown")
        key = "notify_success" if res.get("status") == "ok" else "notify"
        detail[key] = out.get("results", [])
    except Exception:
        pass
    try:
        pm.desktop_toast(title, re.sub(r"^#+ .*$", "", content, flags=re.M).strip())
    except Exception:
        pass


def write_log(res):
    """把每次运行结果追加写入脚本同目录的 checkin.log（不含凭据）。"""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), LOG_NAME)
        line = "%s | status=%s | action=%s | msg=%s\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"),
            res.get("status"), res.get("action"), res.get("msg"))
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# 派猫猫旅行
# ----------------------------------------------------------------------------
def travel(cred, location=None, base=None):
    """旅行状态查询 / 补领 / 派遣（先领后派）。出错静默降级。"""
    base = (base or TRAVEL_DOMAIN_DEFAULT).rstrip("/")
    out = {"base": base, "available": True}
    st, body = get(TRAVEL_STATUS_PATH, cred, base)
    if st in (401, 403):
        out["available"] = False
        out["note"] = "旅行接口身份被拒（HTTP %d），已跳过旅行环节。" % st
        return out
    data = (body or {}).get("data") or {}
    out["state"] = data.get("state")
    if data.get("state") == "arrived":
        st2, b2 = post(TRAVEL_CLAIM_PATH, cred, {}, base)
        out["claim"] = {"http": st2, "code": (b2 or {}).get("code")}
        st, body = get(TRAVEL_STATUS_PATH, cred, base)
        data = (body or {}).get("data") or {}
        out["state"] = data.get("state")
    if data.get("state") == "idle" and not data.get("daily_limit_reached"):
        payload = {"location_id": location} if location else {}
        st3, b3 = post(TRAVEL_DEPART_PATH, cred, payload, base)
        out["depart"] = {"http": st3, "code": (b3 or {}).get("code")}
    elif data.get("state") == "traveling":
        out["note"] = "旅行中，暂不可派遣；到达后下次运行自动补领。"
    elif data.get("state") == "idle" and data.get("daily_limit_reached"):
        out["note"] = "今日派遣已达上限，跳过派遣（次日恢复）。"
    return out


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def checkin(travel_on=True, location=None, domain=None,
            travel_domain=None, notify=True, check_only=False):
    """网页授权通道主流程：状态查询 →（幂等）领取 → 派猫猫旅行 → 推送。"""
    res = {"version": VERSION, "status": "unknown", "action": None,
           "points": None, "balance": None, "msg": "", "detail": {}}
    cred = read_cred()
    res["cred"] = cred
    if not cred:
        res.update(status="no_cred", action="need_setup",
                   msg="尚未配置网页授权凭据，无法签到。" + SETUP_GUIDE)
        return res

    domains = [domain] if domain else probe_domains(cred)
    st, body, used = query_status(cred, domains)
    data = (body or {}).get("data") or {}
    res["detail"]["domain"] = used
    if st in (401, 403):
        res.update(status="auth_failed", action="need_setup",
                   msg="网页授权凭据无效或已过期（HTTP %d），请重新授权一次。" % st)
        res["detail"]["query"] = {"http": st}
        return res
    res["detail"]["query"] = {"http": st, "data": data}
    res["detail"]["streak_days"] = data.get("streak_days")

    if check_only:
        res.update(status="ok", action="check",
                   msg="凭据有效。连续签到 %s 天，今日已签: %s，余额: %s" % (
                       data.get("streak_days", "?"),
                       "是" if data.get("today_checked_in") else "否",
                       data.get("total_credits") or data.get("balance") or "?"))
        res["balance"] = data.get("total_credits") or data.get("balance")
        return res

    # ---- 领取（幂等）----
    if data.get("today_checked_in"):
        res.update(status="ok", action="skip_already_signed",
                   msg="今日已签到，安全跳过（+0）。")
        res["balance"] = data.get("total_credits") or data.get("balance")
    else:
        st2, body2 = post(CHECKIN_PATH, cred, {}, used)
        code = (body2 or {}).get("code")
        if st2 == 200 and code == 0:
            res.update(status="ok", action="clicked", msg="签到成功，+100 积分。")
            res["points"] = 100
            res["balance"] = ((body2 or {}).get("credit")
                              or data.get("total_credits"))
        elif code == 10001 or "已签到" in str((body2 or {}).get("msg", "")):
            res.update(status="ok", action="skip_already_signed",
                       msg="今日已签到，安全跳过。")
        else:
            res.update(status="error", action="checkin_failed",
                       msg="领取失败（HTTP %s）：%s" % (st2, str(body2)[:200]))

    # ---- 派猫猫旅行（先领后派；已签也补领/派遣）----
    if travel_on:
        tv = travel(cred, location, travel_domain)
        res["travel"] = tv
        if tv.get("available"):
            extra = []
            if tv.get("claim"):
                extra.append("已领取旅行积分")
            if tv.get("depart"):
                extra.append("已派出 Buddy")
            if tv.get("note"):
                extra.append(tv["note"])
            if extra:
                res["msg"] = (res.get("msg", "") + " " + "；".join(extra)).strip()

    notify_result(res, suppress=not notify)
    res.pop("cred", None)
    return res


# ----------------------------------------------------------------------------
# 授权配置
# ----------------------------------------------------------------------------
def setup(kind, payload):
    """保存并校验用户提供的授权凭据。kind: code / curl / har / text。"""
    parts = None
    if kind == "har":
        if not payload or not os.path.isfile(payload):
            print(json.dumps({"status": "fail",
                              "msg": "找不到 HAR 文件：%s" % payload},
                             ensure_ascii=False, indent=2))
            return 1
        try:
            parts = parse_har(payload)
        except Exception as e:
            parts = None
            print(json.dumps({"status": "fail",
                              "msg": "HAR 解析失败：%s" % e},
                             ensure_ascii=False, indent=2))
            return 1
        source = "har"
        if parts is None:
            print(json.dumps({
                "status": "fail",
                "msg": "该 HAR 中未找到可用凭据（既无 Bearer，也无带 session 的 200 响应 Cookie）。"
                       "请确认导出时已打开「成长计划 / Buddy 加油站」页面并刷新过。"},
                ensure_ascii=False, indent=2))
            return 1
    else:
        got, fn_name = parse_any(payload)
        if not got:
            print(json.dumps({
                "status": "fail",
                "msg": "未能从提供的内容中识别出凭据。请确认粘贴的是完整授权码"
                       "（WBAUTH1: 开头）、完整 cURL 命令，或 Cookie / Bearer 串。",
                "hint": SETUP_GUIDE}, ensure_ascii=False, indent=2))
            return 1
        parts = got
        source = {"parse_auth_code": "auth-code",
                  "parse_curl": "curl",
                  "parse_bookmarklet_output": "bookmarklet",
                  "parse_raw_credential": "paste"}.get(fn_name, kind)

    cands = build_candidates(parts, source)
    if not cands:
        print(json.dumps({"status": "fail", "msg": "解析结果中没有可用凭据。"},
                         ensure_ascii=False, indent=2))
        return 1

    if not parts.get("ua"):
        note = ("提示：未能从提供内容中读到原始 User-Agent，已使用默认值兜底；"
                "若校验失败（网关会话与 UA 绑定），请改用授权码或 cURL 方式。")
    else:
        note = "已一并取得网关绑定的原始 User-Agent，可正常回放会话。"

    for cred, label in cands:
        ok, base, body = validate_cred(cred)
        if not ok:
            continue
        save_cred({**cred, "domain": base})
        data = (body or {}).get("data") or {}
        print(json.dumps({
            "status": "ok",
            "msg": "授权成功，凭据已保存。",
            "mode": label,
            "cred_masked": describe(cred),
            "ua_masked": (cred.get("user_agent") or "")[:48] + "...",
            "domain": base,
            "used_fields": {"cookie": bool(parts.get("cookie")),
                            "bearer": bool(parts.get("authorization"))},
            "plan": {"streak_days": data.get("streak_days"),
                     "today_checked_in": data.get("today_checked_in"),
                     "balance": data.get("total_credits") or data.get("balance")},
            "cred_file": CRED_FILE,
            "note": note,
        }, ensure_ascii=False, indent=2))
        return 0

    print(json.dumps({
        "status": "fail",
        "msg": "凭据校验未通过（状态接口返回鉴权失败）。未保存。",
        "tried_modes": [label for _, label in cands],
        "note": note,
        "hint": "① 确认浏览器里登录的账号与客户端一致且会话未过期；"
                "② 优先改用授权码 / cURL 方式（会带回原始 UA）；"
                "③ 仍失败时先重新登录网页端再取一次凭据。",
    }, ensure_ascii=False, indent=2))
    return 1


def snippet_help():
    """打印网页授权码工具与用法（工具正文取自 templates/auth-snippet.js）。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "templates", "auth-snippet.js")
    print("=" * 70)
    print("网页授权码工具（一次性授权，约 30 秒）")
    print("=" * 70)
    print("步骤：① 浏览器打开并登录 %s（与客户端同一账号）" % LOGIN_URL)
    print("      ② 按 F12 -> Console，把下面「方式A」整段粘贴回车")
    print("      ③ 授权码已复制到剪贴板，粘贴给助手")
    print("      ④ 助手执行：python web_auth.py --setup-code '<授权码>'")
    print()
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            print(f.read().rstrip())
    else:
        print("未找到模板文件 templates/auth-snippet.js，请检查技能包是否完整。")
    print()
    print("-" * 70)
    print("凭据仅保存在本机：%s" % CRED_FILE)
    print("该工具只在你自己已登录的页面上运行，只把结果写入剪贴板，")
    print("不读取本机任何文件、也不向任何服务器发送数据。")
    return 0


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _read_value(flag):
    argv = sys.argv
    if flag not in argv:
        return None
    try:
        val = argv[argv.index(flag) + 1]
    except Exception:
        return None
    if val == "-":
        try:
            return sys.stdin.read()
        except Exception:
            return None
    return val


def main():
    argv = sys.argv
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    if "--version" in argv:
        print(json.dumps({"version": VERSION}))
        return 0

    if "--snippet" in argv:
        return snippet_help()

    code = _read_value("--setup-code")
    curl = _read_value("--setup-curl")
    har = _read_value("--setup-har")
    sfile = _read_value("--setup-file")
    if sfile:
        try:
            with open(sfile, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            print(json.dumps({"status": "fail", "msg": "读取失败：%s" % e},
                             ensure_ascii=False, indent=2))
            return 1
        return setup("text", text)
    if code is not None:
        return setup("code", code)
    if curl is not None:
        return setup("curl", curl)
    if har is not None:
        return setup("har", har)

    if "--forget" in argv:
        print(json.dumps({"status": "ok", "removed": delete_cred(),
                          "cred_file": CRED_FILE}, ensure_ascii=False, indent=2))
        return 0

    check_only = "--check" in argv
    if "--status" in argv:
        check_only = True
    location = None
    if "--location" in argv:
        try:
            location = int(argv[argv.index("--location") + 1])
        except Exception:
            location = None

    res = checkin(travel_on="--no-travel" not in argv,
                  location=location,
                  domain=_read_value("--domain"),
                  travel_domain=_read_value("--travel-domain"),
                  notify="--no-notify" not in argv,
                  check_only=check_only)
    res.pop("cred", None)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if not check_only:
        write_log(res)
    return 0 if res.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
