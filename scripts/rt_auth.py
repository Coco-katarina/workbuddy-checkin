#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy签到助手 · 长效令牌通道（v3.1.7 新增，通道 C）

与通道 A/B 并列的第三条执行通道，核心能力是「长效令牌自续期」：
  用户一次性提供自己的长效令牌（refresh token）后，本脚本每次运行时
  用它换取新的接口令牌，完成签到与派猫猫旅行，再把轮换出的新长效令牌
  回写本机凭据库，实现长期自续 —— 之后每次执行都不再依赖客户端是否在运行。

与其他通道的关系（由 scripts/auto_checkin.py 统一调度）：
  A) 本地登录态通道（Windows 零配置，需客户端运行）
  B) 网页会话授权通道（macOS 等，一次性授权）
  C) 本通道：适合「客户端不常开 / 定时任务独立运行」的场景

令牌的获取（用户显式发起，本脚本只使用、不自行收集）：
  python rt_auth.py --export-rt --save   # 客户端已登录时，从本机登录态导出并保存
  python rt_auth.py --setup-rt '<令牌>'  # 使用你已保存的令牌字符串录入
  python rt_auth.py --setup-rt-file <文件>

日常使用：
  python rt_auth.py                      # 签到 + 派猫猫闭环（与通道 A 行为一致）
  python rt_auth.py --check              # 只查信息：签到状态/连签天数/积分余额/旅行状态
  python rt_auth.py --no-travel          # 只签到，跳过派猫猫
  python rt_auth.py --forget             # 清除本机保存的令牌（置空覆盖，不删文件）

安全约定：
  - 令牌仅保存在本机 ~/.workbuddy/checkin-rt.json（POSIX 下 0600）；
  - 任何输出（结果 JSON / 日志 / 推送）都不包含令牌明文；
  - 令牌等同账号钥匙，请勿分享；如需撤销，重新登录客户端即可。
"""

import json
import os
import stat
import sys
import time

import urllib.request
import urllib.error

VERSION = "3.1.7"
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 复用通道 A 已有实现：签到 / 旅行 / 推送 / 日志（同目录普通导入）
try:
    import workbuddy_checkin as _wc
except Exception:
    _wc = None

HOME = os.path.expanduser("~")
WORKBUDDY_DIR = os.path.join(HOME, ".workbuddy")
RT_FILE = os.path.join(WORKBUDDY_DIR, "checkin-rt.json")

# 长效令牌刷新端点（插件网关；与客户端刷新所用的同一官方接口）
PLUGIN_API = "https://copilot.tencent.com"
REFRESH_PATH = "/v2/plugin/auth/token/refresh"

# 签到接口基址（与通道 A 相同；已实测同样接受本通道换得的接口令牌）
BILLING_BASE = "https://www.codebuddy.cn/v2"

HTTP_TIMEOUT = 15


# ----------------------------------------------------------------------------
# 凭据读写（与 web_auth.py 同一套约定：0600 / 置空覆盖，不删除文件）
# ----------------------------------------------------------------------------
def read_rt():
    """读取本机保存的长效令牌；不存在或为空返回 None。"""
    try:
        if not os.path.isfile(RT_FILE):
            return None
        with open(RT_FILE, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict) and obj.get("rt"):
            return str(obj["rt"]).strip()
    except Exception:
        return None
    return None


def save_rt(rt, source="manual"):
    """落盘长效令牌（仅本机，POSIX 下 0600）。"""
    os.makedirs(WORKBUDDY_DIR, exist_ok=True)
    obj = {
        "v": 1,
        "rt": rt,
        "source": source,
        "saved_at": int(time.time()),
        "version": VERSION,
    }
    with open(RT_FILE, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(RT_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    return obj


def delete_rt():
    """清除令牌：把文件内容置空（保留空文件，不删除文件本身）。"""
    try:
        if os.path.isfile(RT_FILE):
            with open(RT_FILE, "w", encoding="utf-8") as f:
                f.write("{}")
            return True
    except Exception:
        pass
    return False


def rt_info():
    """令牌库状态（脱敏，不含明文）。"""
    try:
        if not os.path.isfile(RT_FILE):
            return {"configured": False}
        with open(RT_FILE, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return {"configured": False, "corrupt": True}
    rt = obj.get("rt") if isinstance(obj, dict) else None
    if not rt:
        return {"configured": False}
    return {
        "configured": True,
        "source": obj.get("source"),
        "saved_at": obj.get("saved_at"),
        "token_masked": _wc.mask_token(rt) if _wc else (rt[:8] + "..."),
    }


# ----------------------------------------------------------------------------
# 令牌获取（用户显式发起的导出 / 录入）
# ----------------------------------------------------------------------------
def _looks_like_token(s):
    """宽松校验：形似接口返回的长效令牌（base64url JWT 三段式）。"""
    s = (s or "").strip()
    return (len(s) >= 200 and s.count(".") == 2
            and all(ch.isalnum() or ch in "-_.~" for ch in s))


def _load_local_rt():
    """依次尝试所有候选登录态，解密出长效令牌（refresh token）。

    注意与通道 A 的 load_token_best 区分：那个取的是短效接口令牌，
    本函数取的是 auth.refreshToken 字段（两者都是信封时复用同一解密能力）。
    返回 (明文长效令牌, 登录态路径)；全部失败抛 RuntimeError。
    """
    if _wc is None:
        raise RuntimeError("通道 A 脚本缺失")
    errors = []
    for p in _wc._auth_candidates():
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                auth = json.load(f).get("auth", {})
            raw = auth.get("refreshToken")
            if raw is None:
                errors.append((p, "缺少 refreshToken 字段"))
                continue
            return _wc.decrypt_access_token_field(raw), p
        except Exception as e:
            errors.append((p, str(e)[:160]))
    raise RuntimeError(
        "所有候选登录态均无法读取长效令牌（共 %d 个）：\n" % len(errors)
        + "\n".join("  - %s: %s" % (pp, ee) for pp, ee in errors))


def export_rt(save=False):
    """从本机登录态导出长效令牌（用户显式发起）。

    save=False：明文输出到标准输出（便于用户复制到其他工具保管）；
    save=True ：直接保存进本机凭据库，输出仅含脱敏回执。
    复用通道 A 的本地读取能力；通道 A 不可用时报错并给出替代路径。
    """
    try:
        token, _path = _load_local_rt()
    except Exception as e:
        sys.stderr.write(
            "本机登录态当前不可用（%s）。\n"
            "请在 WorkBuddy 客户端已登录的电脑上重试，"
            "或改用 --setup-rt '<令牌>' 手动录入。\n" % str(e)[:240])
        return 1
    if save:
        save_rt(token, source="export")
        print(json.dumps({
            "status": "ok",
            "action": "rt_saved",
            "token_masked": _wc.mask_token(token),
            "file": RT_FILE,
            "note": "长效令牌已保存到本机，之后运行 auto_checkin.py 即可自动签到。",
        }, ensure_ascii=False, indent=2))
    else:
        # 用户显式要求导出明文，供自行保管/录入其他设备
        sys.stdout.write(token + "\n")
        sys.stderr.write(
            "[提示] 以上为你的长效令牌明文（等同账号钥匙），请妥善保管，勿分享给他人。\n")
    return 0


def setup_rt(value):
    if not _looks_like_token(value):
        sys.stderr.write("录入失败：内容不像有效的长效令牌"
                         "（应为一整段较长的 base64 串，含两个点号）。\n")
        return 1
    save_rt(value.strip(), source="manual")
    print(json.dumps({
        "status": "ok",
        "action": "rt_saved",
        "token_masked": _wc.mask_token(value) if _wc else "***",
        "file": RT_FILE,
    }, ensure_ascii=False, indent=2))
    return 0


# ----------------------------------------------------------------------------
# 令牌刷新与主流程
# ----------------------------------------------------------------------------
def refresh_token(rt):
    """用长效令牌换取新的接口令牌。返回 dict（接口原始应答）。"""
    req = urllib.request.Request(PLUGIN_API + REFRESH_PATH,
                                 data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Refresh-Token", rt)
    req.add_header("X-Auth-Refresh-Source", "plugin")
    req.add_header("X-Domain", "copilot.tencent.com")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return {"code": -1, "msg": "HTTP %s" % e.code}
    except Exception as e:
        return {"code": -1, "msg": str(e)[:200]}


def run_rt(check_only=False, do_checkin=True, travel_mode="auto",
           location_id=None):
    """主流程：长效令牌 → 换新接口令牌 →（可选）签到 →（可选）派猫猫。"""
    result = {"status": "unknown", "action": None, "points": None,
              "balance": None, "msg": "", "detail": {"channel": "rt"}}

    rt = read_rt()
    if not rt:
        result.update(status="need_setup", action="need_rt_setup",
                      msg="尚未配置长效令牌：先运行 "
                          "`python scripts/rt_auth.py --export-rt --save` "
                          "或 --setup-rt 录入，之后即可全自动签到与派遣。")
        return result

    r = refresh_token(rt)
    if r.get("code") != 0 or not isinstance(r.get("data"), dict):
        result.update(
            status="error", msg="长效令牌刷新失败 code=%s %s（令牌可能已失效，"
                                "请重新导出或录入）" % (r.get("code"), r.get("msg") or ""))
        return result

    data = r["data"]
    at = data.get("accessToken") or ""
    new_rt = data.get("refreshToken") or ""
    if not at:
        result.update(status="error", msg="刷新应答缺少接口令牌，请稍后重试")
        return result

    # 轮换出的新长效令牌回写本机（写失败不影响本次签到；旧令牌仍可用作兜底）
    rotated = bool(new_rt and new_rt != rt)
    if rotated:
        try:
            save_rt(new_rt, source="refresh")
        except Exception:
            result["detail"]["rt_rewritten"] = False
    result["detail"]["rt_rotated"] = rotated
    result["detail"]["token_masked"] = _wc.mask_token(at) if _wc else "***"

    # ---- 签到（复用通道 A 实现；已实测本通道令牌被同一接口接受）----
    if do_checkin:
        ck = _wc._do_checkin(BILLING_BASE, at, check_only)
        result["status"] = ck.get("status", result["status"])
        result["action"] = ck.get("action")
        result["points"] = ck.get("points")
        result["balance"] = ck.get("balance")
        result["msg"] = ck.get("msg", "")
        result["detail"].update(ck.get("detail", {}))

    # ---- 派猫猫旅行（复用通道 A 实现与既有判断逻辑）----
    if travel_mode != "off":
        travel = _wc._run_travel(at, auto=(travel_mode == "auto"),
                                 location_id=location_id)
        result["travel"] = travel
        if travel.get("available"):
            result["msg"] = _wc._append_travel_msg(result.get("msg", ""), travel)
        if not do_checkin:
            result["status"] = "ok" if travel.get("available") else "error"
            result["action"] = "travel"
            if not travel.get("available"):
                result["msg"] = "查询派猫猫旅行状态失败（请确认令牌有效且网络正常）"

    return result


def _need_setup_json():
    print(json.dumps({
        "version": VERSION,
        "status": "need_setup",
        "action": "need_rt_setup",
        "msg": "本通道需要一次性配置你的长效令牌（等同账号钥匙，仅保存在本机）。",
        "steps": [
            "1. 确认 WorkBuddy 客户端已在本机登录",
            "2. 运行 `python scripts/rt_auth.py --export-rt --save`"
            "（自动读取本机登录态并保存长效令牌，无需手动查看任何文件）",
            "3. 或使用你已保管的令牌：`python scripts/rt_auth.py --setup-rt '<令牌>'`"
            " / `--setup-rt-file <文件>`",
            "4. 配置一次后，运行 `python scripts/rt_auth.py` 即可自动签到与派遣，"
            "且不再要求客户端处于运行状态",
        ],
        "note": "令牌仅保存在本机（0600）；--forget 可随时置空清除。",
    }, ensure_ascii=False, indent=2))


def main():
    argv = sys.argv[1:]
    if "--version" in argv:
        print("rt_auth %s" % VERSION)
        return 0
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    if "--info" in argv:
        print(json.dumps({"version": VERSION, "rt": rt_info()},
                         ensure_ascii=False, indent=2))
        return 0
    if "--forget" in argv:
        ok = delete_rt()
        print(json.dumps({"status": "ok" if ok else "noop",
                          "action": "rt_forget",
                          "note": "已置空覆盖本机令牌文件。"},
                         ensure_ascii=False, indent=2))
        return 0
    if "--export-rt" in argv:
        return export_rt(save=("--save" in argv))
    if "--setup-rt" in argv:
        try:
            idx = argv.index("--setup-rt")
            val = argv[idx + 1]
        except (IndexError, ValueError):
            sys.stderr.write("--setup-rt 后需跟令牌字符串。\n")
            return 1
        return setup_rt(val)
    if "--setup-rt-file" in argv:
        try:
            idx = argv.index("--setup-rt-file")
            path = argv[idx + 1]
            with open(path, "r", encoding="utf-8") as f:
                val = f.read().strip()
        except (IndexError, ValueError, OSError) as e:
            sys.stderr.write("--setup-rt-file 后需跟有效的令牌文件路径（%s）。\n"
                             % str(e)[:120])
            return 1
        return setup_rt(val)

    if _wc is None:
        sys.stderr.write("通道 A 脚本缺失，本通道无法运行。\n")
        return 1

    check_only = ("--check-only" in argv or "--check" in argv
                  or "--status" in argv)
    no_notify = "--no-notify" in argv
    no_travel = "--no-travel" in argv
    travel_only = "travel" in argv
    travel_auto_flag = "--travel-auto" in argv

    if "--push-channels" in argv:
        try:
            idx = argv.index("--push-channels")
            _wc._PUSH_CHANNELS = [c.strip() for c in
                                  argv[idx + 1].split(",") if c.strip()]
        except (IndexError, ValueError):
            sys.stderr.write("[push] --push-channels 后需跟逗号分隔的渠道名\n")
    if "--confirm-paid" in argv:
        _wc._PUSH_CONFIRM_PAID = True

    location_id = None
    if "--location" in argv:
        try:
            idx = argv.index("--location")
            location_id = int(argv[idx + 1])
        except (ValueError, IndexError):
            sys.stderr.write("[travel] --location 需为整数（1-4），已忽略\n")

    if travel_only:
        do_checkin = False
        travel_mode = "auto" if travel_auto_flag else "readonly"
    else:
        do_checkin = True
        if no_travel:
            travel_mode = "off"
        elif check_only:
            travel_mode = "readonly"
        else:
            travel_mode = "auto"

    try:
        res = run_rt(check_only=check_only, do_checkin=do_checkin,
                     travel_mode=travel_mode, location_id=location_id)
    except Exception as e:
        res = {"status": "error", "action": None, "points": None,
               "msg": "脚本未捕获异常: %s" % e, "detail": {"channel": "rt"}}

    if res.get("status") == "need_setup":
        print(json.dumps(res, ensure_ascii=False, indent=2))
        _need_setup_json()
        return 1

    if not no_notify and res.get("status") != "ok":
        try:
            _wc._push_failure(res)
        except Exception:
            pass
    if not no_notify and res.get("status") == "ok":
        try:
            _wc._push_success(res)
        except Exception:
            pass
    if not no_notify:
        try:
            _wc.notify_system(res)
        except Exception:
            pass

    print(json.dumps(res, ensure_ascii=False, indent=2))
    try:
        _wc.write_log(res)
    except Exception:
        pass
    return 0 if res.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
