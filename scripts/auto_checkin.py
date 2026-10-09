#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy签到助手 · 统一入口（v3.1.7）

在本技能的三条通道之间自动择一执行，用户无需自行判断走哪条：

  A) 本地登录态通道 —— scripts/workbuddy_checkin.py
     适用：本机能直接用登录态换取接口令牌的场景（典型为 Windows）。
     一次对话即可完成，无需任何额外配置；要求客户端处于运行状态。

  B) 网页会话授权通道 —— scripts/web_auth.py
     适用：本机登录态无法在本地自动解密的场景（典型为 macOS 标准安装）。
     需用户一次性授权（约 30 秒），之后长期自动签到。

  C) 长效令牌通道 —— scripts/rt_auth.py（v3.1.7 新增）
     适用：已一次性保存长效令牌的场景（`rt_auth.py --export-rt --save` 或
     `--setup-rt`）。每次运行自动用长效令牌换取新的接口令牌并回写轮换结果，
     长期自续；不再要求客户端处于运行状态，适合定时任务独立运行。

选择规则（默认）：
  1. 先探测通道 A 能否在本机直接取到令牌；能则直接执行 A。
  2. 否则若已保存过网页授权凭据，则执行 B（由 B 自行校验并给出结论）。
  3. 否则若已保存过长效令牌，则执行 C（自动续期并完成签到与派遣）。
  4. 三者都不可用时，输出一段可直接转述给用户的配置指引，不做任何修改。

命令行：
  （不带通道参数）   自动择一执行
  --local            强制走通道 A（本地登录态）
  --web              强制走通道 B（网页会话授权）
  --rt               强制走通道 C（长效令牌）
  --probe            只报告三条通道的可用性，不执行签到
  --snippet          打印网页授权码工具与用法（等价于 web_auth.py --snippet）
  其余参数（--check-only / --no-travel / --no-notify / --location /
  --push-channels / --domain / --travel-domain 等）原样透传给对应通道的脚本。
"""

import json
import os
import sys

VERSION = "3.1.7"
HERE = os.path.dirname(os.path.abspath(__file__))

CHANNEL_FLAGS = ("--web", "--local", "--rt", "--probe")


def _load_local():
    """导入同目录的通道 A 脚本；缺失时返回 None。"""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import workbuddy_checkin
    except Exception:
        return None
    return workbuddy_checkin


def _load_web():
    """导入同目录的通道 B 脚本；缺失时返回 None。"""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import web_auth
    except Exception:
        return None
    return web_auth


def _load_rt():
    """导入同目录的通道 C 脚本；缺失时返回 None。"""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import rt_auth
    except Exception:
        return None
    return rt_auth


def _delegate(module, argv):
    """把参数交给指定通道的 CLI 主函数执行，并原样带回其退出码。"""
    if module is None:
        sys.stderr.write("[auto] 目标通道脚本缺失，无法执行。\n")
        return 1
    old_argv = sys.argv[:]
    sys.argv = [getattr(module, "__file__", module.__name__)] + list(argv)
    try:
        rc = module.main()
    except SystemExit as e:
        code = e.code
        if code is None:
            rc = 0
        elif isinstance(code, int):
            rc = code
        else:
            rc = 1
    except Exception as e:
        sys.stderr.write("[auto] 通道执行异常：%s\n" % e)
        rc = 1
    finally:
        sys.argv = old_argv
    return rc


def probe_local(module):
    """只读探测：本机登录态能否直接换取令牌。返回 (ok, detail)。"""
    if module is None:
        return False, "通道 A 脚本缺失"
    try:
        module.load_token_best()
        return True, "可直接使用本机登录态"
    except Exception as e:
        return False, str(e)[:240]


def probe_web(module):
    """只读探测：网页授权凭据是否存在且当前有效。返回 (state, detail)。"""
    if module is None:
        return "missing", "通道 B 脚本缺失"
    cred = module.read_cred()
    if not cred:
        return "unconfigured", "尚未进行网页授权"
    try:
        ok, base, _ = module.validate_cred(cred)
    except Exception as e:
        return "invalid", "校验异常：%s" % str(e)[:160]
    if ok:
        return "ok", "凭据有效（%s）" % base
    return "invalid", "凭据无效或已过期，需要重新授权"


def probe_rt(module):
    """只读探测（不联网）：长效令牌是否已保存。返回 (state, detail)。"""
    if module is None:
        return "missing", "通道 C 脚本缺失"
    info = module.rt_info()
    if info.get("configured"):
        return "ok", "已保存长效令牌（%s）" % info.get("token_masked", "***")
    return "unconfigured", "尚未保存长效令牌"


def _next_step(web_mod, rt_mod):
    """三条通道都不可用时输出给用户的下一步（可直接转述）。"""
    if rt_mod is not None:
        steps = [
            "1. 确认 WorkBuddy 客户端已在本机登录",
            "2. 运行 `python scripts/rt_auth.py --export-rt --save`"
            "（自动读取本机登录态并保存长效令牌）",
            "3. 之后直接运行 `python scripts/auto_checkin.py` 即可自动签到与派遣，"
            "且不要求客户端保持运行",
        ]
        alt = ("客户端不可用的设备上，可用你已保管的令牌录入："
               "`python scripts/rt_auth.py --setup-rt '<令牌>'`；"
               "或走网页授权（见 web_auth.py --snippet）。")
    else:
        steps = [
            "1. 浏览器打开并登录 https://www.workbuddy.cn（与客户端同一账号）",
            "2. 运行 `python scripts/web_auth.py --snippet` 取得授权码工具",
            "3. 在已登录页面按 F12 打开 Console，粘贴运行该工具，授权码会复制到剪贴板",
            "4. 运行 `python scripts/web_auth.py --setup-code '<授权码>'` 完成授权",
            "5. 之后直接运行 `python scripts/auto_checkin.py` 即可自动签到",
        ]
        alt = ("若页面会话 Cookie 为 HttpOnly 导致读不到令牌，"
               "改用开发者工具「Copy as cURL」或导出 HAR 的方式授权："
               "`python scripts/web_auth.py --setup-curl '<cURL>'` / "
               "`--setup-har <文件>`")
    print(json.dumps({
        "version": VERSION,
        "status": "need_setup",
        "action": "need_authorization",
        "msg": "本机登录态无法直接换取接口令牌，需要完成一次下述配置，之后即可长期自动签到。",
        "steps": steps,
        "alternative": alt,
        "note": "配置只需一次；凭据仅保存在本机，可随时用对应通道的 `--forget` 清除。",
    }, ensure_ascii=False, indent=2))
    return 1


def main():
    argv = sys.argv[1:]
    if "--version" in argv:
        print(json.dumps({"version": VERSION}))
        return 0
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    passthrough = [a for a in argv if a not in CHANNEL_FLAGS
                   and a not in ("--version", "--help", "-h")]
    # 带值的参数需连同其值一起透传，避免值被当成位置参数
    values = []
    for flag in ("--location", "--domain", "--travel-domain",
                 "--push-channels"):
        if flag in argv:
            try:
                values.append(argv[argv.index(flag) + 1])
            except Exception:
                pass
    passthrough += [v for v in values if v not in passthrough]

    local = _load_local()
    web = _load_web()
    rt = _load_rt()

    if "--snippet" in argv:
        return _delegate(web, ["--snippet"])

    if "--probe" in argv:
        lok, lwhy = probe_local(local)
        wst, wwhy = probe_web(web)
        rst, rwhy = probe_rt(rt)
        print(json.dumps({
            "version": VERSION,
            "channel_local": {"available": lok, "detail": lwhy},
            "channel_web": {"state": wst, "detail": wwhy},
            "channel_rt": {"state": rst, "detail": rwhy},
            "recommendation": ("channel_local" if lok else
                               "channel_web" if wst == "ok" else
                               "channel_rt" if rst == "ok" else "need_setup"),
        }, ensure_ascii=False, indent=2))
        return 0

    if "--web" in argv:
        return _delegate(web, passthrough)
    if "--local" in argv:
        return _delegate(local, passthrough)
    if "--rt" in argv:
        return _delegate(rt, passthrough)

    # 默认：先本地，后网页授权，再长效令牌
    lok, _ = probe_local(local)
    if lok:
        return _delegate(local, passthrough)
    if web is not None and web.read_cred():
        return _delegate(web, passthrough)
    if rt is not None and rt.read_rt():
        return _delegate(rt, passthrough)
    return _next_step(web, rt)


if __name__ == "__main__":
    sys.exit(main())
