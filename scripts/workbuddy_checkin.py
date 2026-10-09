#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy签到助手（每日自动签到脚本，接口直签，无需 GUI 点击 / OCR）

原理：
  1. 读取本机 WorkBuddy 登录态文件中的 accessToken（只读，绝不修改登录态）
  2. 查询今日签到状态  POST {base}/billing/meter/checkin-activity-status
  3. 若今日未签到，调用 POST {base}/billing/meter/daily-checkin 领取
  4. 已签到 / 接口返回 code=10001 则安全跳过，不做重复领取
  5. 派猫猫旅行（默认随签到一起跑全自动闭环「先领后派」）：
     查询 https://www.workbuddy.cn/activity/growth/buddy/travel/status
     已到达(arrived) → POST .../travel/claim 领取旅行积分
     空闲(idle)且未达每日上限 → POST .../travel/depart 派出 Buddy
     旅行中(traveling) → 不派遣，仅展示到达倒计时
     注意：旅行接口域名是 www.workbuddy.cn（与签到的 www.codebuddy.cn 不同），
           且路径不带 /v2 前缀。

【v3.1.0 关键修复 —— 兼容 WorkBuddy 5.6.2+ 客户端】
  5.6.2 客户端把 AtRestEncryption 的 buildMode 从 disabled 改为 required，登录态
  workbuddy-desktop.info 里的 auth.accessToken（及 refreshToken 等敏感字段）由明文 JWT
  变成 AES-256-GCM 信封 {"$wbEncrypted":1,"envelope":"<base64>"}。旧脚本把该字段当明文
  JWT 读取 → 鉴权失败 → 技能无法签到。
  本版本新增 load_token() 的「读端」兼容解密（见脚本下部的 AtRestEncryption 解密段）：
  自动识别明文 / 信封两种情况，对信封用从客户端复刻的 AES-256-GCM 算法解密。
  atRestSecretKey 按多级策略自动定位：环境变量 → 密钥文件 → DPAPI blob 扫描 →
  运行中 WorkBuddy.exe 进程内存扫描（5.6.2 密钥不落盘、只驻留客户端内存时的主路径，
  需客户端已启动登录且与脚本同一 Windows 用户）。

失败推送（可选）：
  若签到结果为 status!=ok，会读取本地配置文件
  ~/.workbuddy/scripts/notify_config.json（若存在），向已配置的渠道推送失败提醒。
  支持 12 类渠道：企业微信/钉钉/飞书/微信(PushPlus)/邮件/短信/QQ/Slack/Telegram/Bark/通用 Webhook/系统通知。
  配置缺失的渠道自动跳过，单渠道失败不影响其他渠道，不影响签到。

成功推送（可选，默认关闭）：
  在 notify_config.json 中设置 "success_notify": true 后，
  签到成功（本次新签到 / 今日已签跳过）也会向已配置渠道推送一条播报。
  默认不开启，保持「静默无打扰」；仅失败时提醒。
  推送逻辑由 scripts/push_message.py 提供（接口/配置项对齐 totorosir-push-message 技能）。

安全约定：
  - 不打印 token / accessToken / refreshToken（任何输出都不含敏感凭据）
  - 不修改本机登录态文件
  - 推送密钥只存在于本地 notify_config.json，永不进入脚本或技能目录
  - 异常只记录失败原因，最多重试 1 次，不无限重试
  - 解密信封是「只读」操作：只解开本机已登录态里的 token，绝不回写、绝不外传

用法：
  python workbuddy_checkin.py                  # 签到 + 派猫猫旅行全自动闭环（默认）
  python workbuddy_checkin.py --no-travel      # 只签到，跳过旅行（最快）
  python workbuddy_checkin.py --check-only     # 仅查询状态（只读，不领取、不写旅行）
  python workbuddy_checkin.py travel           # 只查派猫猫旅行状态（只读）
  python workbuddy_checkin.py travel --travel-auto      # 只跑旅行闭环（不签到）
  python workbuddy_checkin.py --travel-auto    # 显式开启旅行闭环（默认已开）
  python workbuddy_checkin.py --location N     # 指定派遣地点（1-4，缺省随机）
  python workbuddy_checkin.py --no-notify      # 跳过全部推送与桌面通知（调试用）
  python workbuddy_checkin.py --diagnose       # 环境自检（Python/登录态/网络/桌面会话/推送配置）
  python workbuddy_checkin.py --init-config    # 生成 notify_config.json.example 模板
  python workbuddy_checkin.py --self-test-atrest   # 仅自测 5.6.2 信封解密算法（不联网、不读登录态）
  python workbuddy_checkin.py --help           # 显示帮助
  python workbuddy_checkin.py --version        # 显示版本
  成功推送开关见 ~/.workbuddy/scripts/notify_config.json 的 "success_notify"
"""

import sys

# Python 版本守卫：太旧时给出友好提示，而不是抛出一堆堆栈
if sys.version_info < (3, 6):
    sys.stderr.write(
        "WorkBuddy签到助手：需要 Python 3.6+，当前为 %s。\n"
        "请安装 Python 3.8+（https://www.python.org/downloads/，勾选 Add to PATH），\n"
        "或直接使用 WorkBuddy 自带的托管 Python（~/.workbuddy/binaries/python）。\n"
        % sys.version.split()[0])
    sys.exit(2)

import base64
import hashlib
import json
import os
import random
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

# 多渠道消息推送模块（接口/配置项对齐 totorosir-push-message 技能，自包含、零依赖）
try:
    import push_message  # 与本脚本同目录
except Exception:  # noqa: BLE001 - 模块缺失时回落到静默，不影响签到
    push_message = None  # type: ignore

# ---- 配置 ----
def _auth_candidates():
    """按操作系统返回 WorkBuddy 登录态文件候选路径，依次探测。"""
    home = os.path.expanduser("~")
    cands = []
    if sys.platform.startswith("win"):
        for env in ("LOCALAPPDATA", "APPDATA"):
            base = os.environ.get(env, "")
            if base:
                cands.append(os.path.join(
                    base, "CodeBuddyExtension", "Data", "Public",
                    "auth", "workbuddy-desktop.info"))
    elif sys.platform == "darwin":
        cands.append(os.path.join(
            home, "Library", "Application Support", "CodeBuddyExtension",
            "Data", "Public", "auth", "workbuddy-desktop.info"))
    else:  # linux / 其他类 Unix
        cands.append(os.path.join(
            home, ".config", "CodeBuddyExtension", "Data", "Public",
            "auth", "workbuddy-desktop.info"))
    # 兜底：便携版 / 未知布局（与 ~/.workbuddy 同根）
    cands.append(os.path.join(
        home, ".workbuddy", "auth", "workbuddy-desktop.info"))
    return [p for p in cands if p]
STATUS_PATH = "/billing/meter/checkin-activity-status"
CHECKIN_PATH = "/billing/meter/daily-checkin"
HTTP_TIMEOUT = 10
MAX_RETRY = 1
VERSION = "3.1.7"

# 推送渠道覆盖（由命令行 --push-channels 设定，None=用配置里全部已就绪渠道）
_PUSH_CHANNELS = None
_PUSH_CONFIRM_PAID = False

# ---- 派猫猫旅行配置 ----
# 关键：旅行接口与签到接口不是同一个域名，且路径不带 /v2 前缀，写错一律 404。
TRAVEL_BASE = "https://www.workbuddy.cn"
TRAVEL_STATUS_PATH = "/activity/growth/buddy/travel/status"   # GET  旅行状态
TRAVEL_CONFIG_PATH = "/activity/growth/buddy/travel/config"   # GET  可选地点
TRAVEL_CLAIM_PATH = "/activity/growth/buddy/travel/claim"     # POST 领取旅行积分
TRAVEL_DEPART_PATH = "/activity/growth/buddy/travel/depart"   # POST 派出 Buddy
TRAVEL_STATE_TEXT = {
    "idle": "空闲（可派遣）",
    "traveling": "旅行中",
    "arrived": "已到达，待领取",
}
# 失败推送配置（含密钥，仅本地，不入库）
NOTIFY_CONFIG = os.path.join(os.path.expanduser("~"),
                             ".workbuddy", "scripts", "notify_config.json")


def find_auth_file():
    for p in _auth_candidates():
        if os.path.isfile(p):
            return p
    return None


def _check_expiry(auth):
    """登录态已过期时给出友好提示（只读取 expiresAt，绝不打印 token）。"""
    raw = auth.get("expiresAt")
    if not raw:
        return  # 无过期字段则跳过检查
    try:
        exp = int(raw)
    except (TypeError, ValueError):
        return
    # 兼容 epoch 毫秒（13 位）/ 秒（10 位）
    if exp > 10 ** 11:
        exp = exp / 1000.0
    if exp <= time.time():
        expire_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exp))
        raise RuntimeError(
            "登录态已过期（过期时间 %s），请重新登录 WorkBuddy 客户端后再试" % expire_str)


# =============================================================================
# WorkBuddy 5.6.2+ 登录态 AtRestEncryption 解密（读端兼容，自包含）
# -----------------------------------------------------------------------------
# 背景：5.6.2 客户端把 AtRestEncryption 的 buildMode 从 disabled 改为 required，
# 登录态 workbuddy-desktop.info 中的 auth.accessToken（及 refreshToken 等敏感字段）
# 由明文 JWT 变成 AES-256-GCM 信封： {"$wbEncrypted":1, "envelope":"<base64>"}
# 旧脚本直接把字段当明文 JWT 读取 -> 鉴权失败 -> 技能无法签到。
#
# 本段仅做「读端」兼容解密，复刻客户端 packages/at-rest-crypto 的算法：
#   envelope(suite=1) = {suite, keyId, nonce(b64), authTag(b64), ciphertext(b64)}
#   AES 密钥 key = SHA256(atRestSecretKey 字符串 UTF-8)  -> 32 字节
#   keyId      = SHA256(key 字节).hexdigest()[:16]
#   AAD(sym-v1, framing=field) = 见 _build_field_aad()
# atRestSecretKey 由客户端原生模块 electron.workbuddyStorage.loggerGet() 运行时提供，
# 5.6.2 起不落盘、只驻留运行中的 WorkBuddy.exe 进程内存（同一把密钥也用于包裹
# ~/.workbuddy/keyblob 里的主密钥）。因此这里按多级策略自动定位：
#   环境变量 → 密钥文件 → DPAPI blob 扫描（兼容个别落盘情况）→
#   ReadProcessMemory 扫描客户端进程内存（keyId 定向 + base64 候选严格校验）。
# 密钥材料仅在内存中校验使用，绝不写盘、绝不打印。
# =============================================================================

# ---- 常量（与客户端 at-rest-crypto/dist/key-normalize / field.mjs 完全一致）----
_AAD_DOMAIN = b"WB-AAD\x00"
_FRAMING_CODE = {"file": 1, "field": 2, "record": 3, "stream": 4}
_STANDARD_FORMAT_ID = {"file": "WBEF1", "field": "WBEV1", "record": "WBER1", "stream": "WBES1"}


def _b64_canonical_decode(value, field):
    """复刻 decodeCanonicalBase64：标准 base64、末尾补 =，且与重编码一致才算合法。"""
    if not isinstance(value, str):
        raise ValueError("%s 不是字符串" % field)
    decoded = base64.b64decode(value)
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("%s 不是规范 base64" % field)
    return decoded


def _derive_at_rest_key(at_rest_secret_key):
    """复刻 normalizeAtRestKeyPayload：key=SHA256(secret字符串UTF-8)，keyId=SHA256(key)[:16]。

    返回 (key_32bytes, key_id_16hex)。
    """
    if not isinstance(at_rest_secret_key, str) or len(at_rest_secret_key) != 44:
        raise ValueError("atRestSecretKey 必须是 44 字符规范 base64（32 字节）")
    # 校验是规范 base64 且解码为 32 字节
    raw = _b64_canonical_decode(at_rest_secret_key, "atRestSecretKey")
    if len(raw) != 32:
        raise ValueError("atRestSecretKey 解码后必须是 32 字节")
    key = hashlib.sha256(at_rest_secret_key.encode("utf-8")).digest()
    key_id = hashlib.sha256(key).hexdigest()[:16]
    return key, key_id


def _build_field_aad(key_id):
    """复刻 buildAuthenticatedContextAad(keyId, suite=1, {framing:'field'}, scheme='sym-v1')。"""
    def encode_uint32(v):
        return v.to_bytes(4, "big")
    def encode_length_prefixed(s):
        b = s.encode("utf-8")
        return encode_uint32(len(b)) + b
    return b"".join([
        _AAD_DOMAIN,
        bytes([1]),
        encode_length_prefixed(_STANDARD_FORMAT_ID["field"]),  # "WBEV1"
        encode_length_prefixed("sym-v1"),
        encode_uint32(1),                                       # suite
        encode_length_prefixed(key_id),                          # 16 hex 字符
        bytes([_FRAMING_CODE["field"]]),                         # 2
        bytes([0]),                                              # sequence 缺失
        bytes([0]),                                              # final 缺失
    ])


# ---- AES-256-GCM 后端（cryptography > PyCryptodome > 纯 Python 兜底）----
def _aes_gcm_decrypt(key, nonce, ciphertext, aad, auth_tag):
    """AES-256-GCM 解密，返回明文 bytes；校验失败抛 IntegrityError。"""
    # 1) cryptography（WorkBuddy 托管 Python 自带，优先）
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aes = AESGCM(key)
        return aes.decrypt(nonce, ciphertext + auth_tag, aad)
    except ImportError:
        pass
    # 2) PyCryptodome（常见纯 Python 可安装包）
    try:
        from Crypto.Cipher import AES  # type: ignore
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        cipher.update(aad)
        return cipher.decrypt_and_verify(ciphertext, auth_tag)
    except ImportError:
        pass
    # 3) 纯 Python 兜底（零依赖，已与 cryptography 对拍校验）
    return _aes_gcm_decrypt_pure(key, nonce, ciphertext, aad, auth_tag)


def _aes_gcm_encrypt(key, nonce, plaintext, aad):
    """AES-256-GCM 加密，返回 (ciphertext, auth_tag)；供自测 roundtrip 使用。"""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aes = AESGCM(key)
        ct_and_tag = aes.encrypt(nonce, plaintext, aad)
        return ct_and_tag[:-16], ct_and_tag[-16:]
    except ImportError:
        pass
    try:
        from Crypto.Cipher import AES  # type: ignore
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        cipher.update(aad)
        ct, tag = cipher.encrypt_and_digest(plaintext)
        return ct, tag
    except ImportError:
        pass
    return _aes_gcm_encrypt_pure(key, nonce, plaintext, aad)


# ---- 纯 Python AES-256-GCM 实现（仅作兜底，已与 cryptography 对拍）----
def _aes_gcm_decrypt_pure(key, nonce, ciphertext, aad, auth_tag):
    pt = _aes_gcm_pure(key, nonce, ciphertext, aad, auth_tag, decrypt=True)
    return pt


def _aes_gcm_encrypt_pure(key, nonce, plaintext, aad):
    ct, tag = _aes_gcm_pure(key, nonce, plaintext, aad, None, decrypt=False)
    return ct, tag


def _aes_gcm_pure(key, nonce, data, aad, auth_tag_in, decrypt):
    # AES 密钥扩展
    Nk = len(key) // 4
    Nr = Nk + 6
    RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]
    SBOX = _aes_sbox()
    w = [list(key[4 * i:4 * i + 4]) for i in range(Nk)]
    for i in range(Nk, 4 * (Nr + 1)):
        temp = list(w[i - 1])
        if i % Nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[b] for b in temp]
            temp[0] ^= RCON[i // Nk - 1]
        elif Nk > 6 and i % Nk == 4:
            temp = [SBOX[b] for b in temp]
        w.append([w[i - Nk][j] ^ temp[j] for j in range(4)])
    round_keys = [[w[i][j] for i in range(4 * (Nr + 1))] for j in range(4)]

    def xtime(a):
        a <<= 1
        if a & 0x100:
            a ^= 0x11B
        return a & 0xFF

    def gmul(a, b):
        p = 0
        for _ in range(8):
            if b & 1:
                p ^= a
            hi = a & 0x80
            a = (a << 1) & 0xFF
            if hi:
                a ^= 0x1B
            b >>= 1
        return p & 0xFF

    def add_round_key(state, rnd):
        for c in range(4):
            for r in range(4):
                state[r][c] ^= round_keys[r][4 * rnd + c]

    def sub_bytes(state):
        for r in range(4):
            for c in range(4):
                state[r][c] = SBOX[state[r][c]]

    def shift_rows(state):
        state[1] = state[1][1:] + state[1][:1]
        state[2] = state[2][2:] + state[2][:2]
        state[3] = state[3][3:] + state[3][:3]

    def mix_columns(state):
        for c in range(4):
            a = [state[r][c] for r in range(4)]
            state[0][c] = gmul(a[0], 2) ^ gmul(a[1], 3) ^ a[2] ^ a[3]
            state[1][c] = a[0] ^ gmul(a[1], 2) ^ gmul(a[2], 3) ^ a[3]
            state[2][c] = a[0] ^ a[1] ^ gmul(a[2], 2) ^ gmul(a[3], 3)
            state[3][c] = gmul(a[0], 3) ^ a[1] ^ a[2] ^ gmul(a[3], 2)

    def cipher_block(block):
        state = [[block[r + 4 * c] for c in range(4)] for r in range(4)]
        add_round_key(state, 0)
        for rnd in range(1, Nr):
            sub_bytes(state)
            shift_rows(state)
            mix_columns(state)
            add_round_key(state, rnd)
        sub_bytes(state)
        shift_rows(state)
        add_round_key(state, Nr)
        return bytes(state[r][c] for c in range(4) for r in range(4))

    # GCM
    H = cipher_block(b"\x00" * 16)
    # GHASH
    def gf_mult(x, y):
        R = 0xE1000000000000000000000000000000
        z = 0
        v = y
        for i in range(128):
            if (x >> (127 - i)) & 1:
                z ^= v
            if v & 1:
                v = (v >> 1) ^ R
            else:
                v >>= 1
        return z

    def ghash_blocks(blocks):
        y = 0
        for blk in blocks:
            x = int.from_bytes(blk, "big")
            y = gf_mult(y ^ x, int.from_bytes(H, "big"))
        return y.to_bytes(16, "big")

    # 构造计数器块：nonce(12) + 32位计数（从 1 开始）
    def ctr_block(counter):
        cb = nonce + counter.to_bytes(4, "big")
        return cipher_block(cb)

    # 分块（16 字节，末尾补零），空输入返回 []
    def split_blocks(b):
        if not b:
            return []
        pad = b + b"\x00" * ((16 - len(b) % 16) % 16)
        return [pad[i:i + 16] for i in range(0, len(pad), 16)]

    # CTR 加/解密（对称：明文 ^ 密钥流 = 密文）
    # 关键：数据计数器从 inc32(J0) 开始，即 nonce||0x00000002（J0=nonce||0x00000001 只用于给 tag 做 E_K）
    aad_blocks = split_blocks(aad)
    if decrypt:
        ct_blocks = split_blocks(data)          # data = 密文
        out = bytearray()
        for i, blk in enumerate(ct_blocks):
            ks = ctr_block(i + 2)
            out.extend(bytes(blk[j] ^ ks[j] for j in range(len(blk))))
        out = bytes(out[:len(data)])
        ct_for_ghash = data
    else:
        pt_blocks = split_blocks(data)          # data = 明文
        ct = bytearray()
        for i, blk in enumerate(pt_blocks):
            ks = ctr_block(i + 2)
            ct.extend(bytes(blk[j] ^ ks[j] for j in range(len(blk))))
        ct = bytes(ct[:len(data)])
        out = ct
        ct_for_ghash = ct

    # GHASH 必须基于"密文"计算（加密时即 CTR 输出；解密时即输入密文）
    ghash_input = aad_blocks + split_blocks(ct_for_ghash)
    ghash_input.append((len(aad) * 8).to_bytes(8, "big") + (len(ct_for_ghash) * 8).to_bytes(8, "big"))
    S = ghash_blocks(ghash_input)

    # J0：12 字节 nonce 时 J0 = nonce || 0x00000001；其它长度按 GHASH(nonce) 处理
    if len(nonce) == 12:
        j0 = nonce + b"\x00\x00\x00\x01"
    else:
        j0 = ghash_blocks(split_blocks(nonce) + [(len(nonce) * 8).to_bytes(16, "big")])
    ek_j0 = cipher_block(j0)
    tag = bytes(S[k] ^ ek_j0[k] for k in range(16))

    if decrypt:
        if auth_tag_in is not None and int.from_bytes(tag, "big") != int.from_bytes(auth_tag_in, "big"):
            raise ValueError("ciphertext authentication failed (GCM tag mismatch)")
        return out
    else:
        return out, tag


_AES_SBOX_CACHE = None


def _aes_sbox():
    # 纯 Python 兜底用的标准 AES S-Box（公开常量，已验证）。
    return _AES_STD_SBOX


def _rotl(x, n):
    return ((x << n) | (x >> (8 - n))) & 0xFF


def _xtime_lite(x):
    x <<= 1
    if x & 0x100:
        x ^= 0x11B
    return x & 0xFF


def _gmul_lookup(a, b):
    # 仅用于扩展欧几里得，简单实现
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p & 0xFF


# 标准 AES S-Box（公开常量，用于纯 Python 兜底，确保正确性）
_AES_STD_SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]


# ---- 解析信封（复刻 parseSupportedEnvelope，仅 suite=1）----
def _parse_suite1_envelope(envelope_bytes):
    try:
        env = json.loads(envelope_bytes.decode("utf-8"))
    except Exception as e:
        raise ValueError("信封不是合法 JSON: %s" % e)
    if not isinstance(env, dict):
        raise ValueError("信封必须是对象")
    if env.get("suite") != 1:
        raise ValueError("只支持 suite=1，实际 %r" % env.get("suite"))
    key_id = env.get("keyId")
    if not isinstance(key_id, str) or not _re_hex16(key_id):
        raise ValueError("envelope.keyId 非法")
    nonce = _b64_canonical_decode(env["nonce"], "nonce")
    auth_tag = _b64_canonical_decode(env["authTag"], "authTag")
    ct = env.get("ciphertext")
    ciphertext = b"" if (isinstance(ct, str) and ct == "") else _b64_canonical_decode(ct, "ciphertext")
    if len(nonce) != 12 or len(auth_tag) != 16:
        raise ValueError("nonce(12)/authTag(16) 长度错误")
    return key_id, nonce, ciphertext, auth_tag


def _re_hex16(s):
    import re
    return bool(re.fullmatch(r"[0-9a-f]{16}", s))


# ---- 当前用户 DPAPI（Windows）解开 atRestSecretKey 落盘文件 ----
def _dpapi_unprotect(blob):
    """调用 crypt32.CryptUnprotectData（当前用户）解开 DPAPI blob，返回原文 bytes。"""
    if not sys.platform.startswith("win"):
        raise OSError("DPAPI 仅在 Windows 可用")
    import ctypes
    from ctypes import wintypes
    crypt32 = ctypes.windll.crypt32  # type: ignore
    blob_len = len(blob)
    blob_buf = ctypes.create_string_buffer(blob, blob_len)
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
    in_blob = DATA_BLOB(blob_len, blob_buf)
    out_blob = DATA_BLOB()
    res = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob))
    if not res:
        raise OSError("CryptUnprotectData 失败（可能不是当前用户/本机加密的 blob）")
    try:
        size = out_blob.cbData
        ptr = ctypes.cast(out_blob.pbData, ctypes.POINTER(ctypes.c_byte * size))
        data = bytes(ptr.contents[:size])
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)  # type: ignore
    return data


# ---- 5.6.2+ 进程内存密钥发现（atRestSecretKey 只驻留运行中客户端内存、磁盘无落盘文件时使用）----
# WorkBuddy 5.6.2 客户端由原生模块 workbuddyStorage.loggerGet() 运行时提供密钥，
# 密钥不存在于任何文件；可用 ReadProcessMemory 扫描运行中 WorkBuddy.exe 进程内存，
# 定位信封 keyId 的 ASCII 串，提取附近 44 字符 base64 候选并按 sha256 派生 keyId 严格校验。
# 密钥材料仅在内存中校验使用，绝不写盘、绝不打印。
_B64_CANDIDATE_RE = r"[A-Za-z0-9+/]{43}="
_B64_CANDIDATE_TEXT_RE = r"[A-Za-z0-9+/]{43}="
# 同一候选模式的 UTF-16LE 形式（V8/Electron 字符串可能以宽字符驻留内存）
_B64_CANDIDATE_RE_UTF16 = rb"(?:[A-Za-z0-9+/]\x00){43}=\x00"
# 全内存兜底扫描的候选去重上限（防长尾）
_MAX_MEMORY_CANDIDATES = 200000


def _list_workbuddy_pids():
    """枚举运行中的 WorkBuddy 客户端进程 PID（仅 Windows）。"""
    if not sys.platform.startswith("win"):
        return []
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    TH32CS_SNAPPROCESS = 0x2
    INVALID_HANDLE_VALUE = ctypes.c_size_t(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),   # ULONG_PTR
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),         # 注意是 LONG，不是指针
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    names = {"workbuddy.exe"}
    extra = os.environ.get("WORKBUDDY_PROCESS_NAMES", "")
    if extra:
        names |= {n.strip().lower() for n in extra.split(",") if n.strip()}

    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return []
    pids = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            name = entry.szExeFile
            if name and name.lower() in names:
                pids.append(int(entry.th32ProcessID))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return pids


def _iter_process_readable_regions(kernel32, handle, max_total_bytes=3 * 1024 * 1024 * 1024):
    """逐段产出目标进程可读已提交内存块（bytes）。只读，跳过不可读/守护页。"""
    import ctypes
    from ctypes import wintypes

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [("BaseAddress", ctypes.c_void_p),
                    ("AllocationBase", ctypes.c_void_p),
                    ("AllocationProtect", wintypes.DWORD),
                    ("PartitionId", wintypes.DWORD),      # x64 对齐填充
                    ("RegionSize", ctypes.c_size_t),
                    ("State", wintypes.DWORD),
                    ("Protect", wintypes.DWORD),
                    ("Type", wintypes.DWORD),
                    ("PartitionId2", wintypes.DWORD)]     # x64 对齐填充

    kernel32.VirtualQueryEx.restype = ctypes.c_size_t
    kernel32.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    MEM_COMMIT = 0x1000
    PAGE_NOACCESS = 0x01
    PAGE_GUARD = 0x100
    CHUNK = 8 * 1024 * 1024
    total = 0
    addr = 0
    mbi = MEMORY_BASIC_INFORMATION()
    while total < max_total_bytes:
        ret = kernel32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not ret:
            break
        base = mbi.BaseAddress or addr
        region_size = mbi.RegionSize or 0
        if region_size <= 0:
            break
        if mbi.State == MEM_COMMIT and mbi.Protect != PAGE_NOACCESS and not (mbi.Protect & PAGE_GUARD):
            read = 0
            while read < region_size and total < max_total_bytes:
                n = min(CHUNK, region_size - read)
                buf = ctypes.create_string_buffer(n)
                got = ctypes.c_size_t(0)
                ok = kernel32.ReadProcessMemory(handle, ctypes.c_void_p(base + read),
                                                buf, n, ctypes.byref(got))
                if ok and got.value:
                    data = buf.raw[:got.value]
                    total += len(data)
                    yield data
                    read += got.value
                else:
                    # 该子段不可读，跳到区域尾
                    read = region_size
        nxt = base + region_size
        if nxt <= addr:
            break
        addr = nxt


def _key_candidate_matches(candidate_text, target_key_id):
    """校验 44 字符 base64 候选是否派生出目标 keyId。"""
    try:
        _, key_id = _derive_at_rest_key(candidate_text)
        return key_id == target_key_id
    except Exception:
        return False


def _extract_key_near_hits(data, hit_indexes, pattern_len, target_key_id, utf16=False):
    """在 keyId 命中点附近 ±4KB 窗口提取 base64 候选并校验。"""
    import re
    win = 4096
    checked = set()
    for h in hit_indexes:
        lo = max(0, h - win)
        hi = min(len(data), h + pattern_len + win)
        seg = data[lo:hi]
        if utf16:
            for m in re.finditer(_B64_CANDIDATE_TEXT_RE, seg.decode("utf-16-le", "ignore")):
                cand = m.group(0)
                if cand in checked:
                    continue
                checked.add(cand)
                if _key_candidate_matches(cand, target_key_id):
                    return cand
        else:
            for m in re.finditer(_B64_CANDIDATE_RE.encode("ascii", "ignore"), seg):
                cand = m.group(0).decode("ascii")
                if cand in checked:
                    continue
                checked.add(cand)
                if _key_candidate_matches(cand, target_key_id):
                    return cand
    return None


def _discover_at_rest_key_from_memory(target_key_id):
    """扫描运行中 WorkBuddy 客户端进程内存寻找 atRestSecretKey（仅 Windows）。

    策略：
      Pass 1（定向，快速路径）：搜索信封 keyId 的 ASCII / UTF-16LE 串，
                                在命中点 ±4KB 窗口内提取 44 字符 canonical base64 候选并校验；
      Pass 2（兜底）：对全部可读内存做 base64 候选（ASCII 与 UTF-16LE）扫描逐个校验。

    ⚠️ 设计要点：Pass 2 必须无条件执行，不能用「Pass 1 是否命中 keyId」来门控。
      客户端内存中 keyId 的 ASCII 串会因 AAD / 日志等上下文大量出现，而密钥本体未必
      落在这些命中点的 ±4KB 邻域内；若以「是否命中」作门控，就会出现「命中 keyId 但
      取不到密钥 → 跳过全内存兜底 → 误报无法定位」的失败。故 Pass 1 失败后无条件继续
      Pass 2，并补齐 UTF-16LE 候选（V8 字符串可能以宽字符驻留），二者共用去重集合与上限。

    找到返回密钥字符串；找不到返回 None。任何异常都不向外抛（签到主流程不应因扫描崩溃）。
    """
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes
        import re as _re
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        pattern = target_key_id.encode("ascii", "ignore")
        if len(pattern) != 16:
            return None
        utf16_pattern = target_key_id.encode("utf-16-le")
        b64_ascii_re = _re.compile(_B64_CANDIDATE_RE.encode("ascii", "ignore"))
        b64_utf16_re = _re.compile(_B64_CANDIDATE_RE_UTF16)
        pids = _list_workbuddy_pids()
        if not pids:
            return None
        seen_candidates = set()
        for pid in pids:
            handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
            if not handle:
                continue
            try:
                for data in _iter_process_readable_regions(kernel32, handle):
                    # ---- Pass 1：keyId 邻域快速路径（ASCII 与 UTF-16LE 各试一次）----
                    for pat, is_u16 in ((pattern, False), (utf16_pattern, True)):
                        hits = []
                        start = 0
                        while True:
                            i = data.find(pat, start)
                            if i < 0:
                                break
                            hits.append(i)
                            start = i + 1
                        if hits:
                            key = _extract_key_near_hits(
                                data, hits, len(pat), target_key_id, utf16=is_u16)
                            if key:
                                return key
                    # ---- Pass 2：全内存候选兜底（不再被 Pass 1 命中与否门控）----
                    for m in b64_ascii_re.finditer(data):
                        cand = m.group(0).decode("ascii")
                        if cand in seen_candidates:
                            continue
                        seen_candidates.add(cand)
                        if len(seen_candidates) > _MAX_MEMORY_CANDIDATES:
                            return None
                        if _key_candidate_matches(cand, target_key_id):
                            return cand
                    for m in b64_utf16_re.finditer(data):
                        cand = m.group(0).decode("utf-16-le")
                        if cand in seen_candidates:
                            continue
                        seen_candidates.add(cand)
                        if len(seen_candidates) > _MAX_MEMORY_CANDIDATES:
                            return None
                        if _key_candidate_matches(cand, target_key_id):
                            return cand
            finally:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
        return None
    except Exception:
        return None


def _discover_at_rest_key(target_key_id):
    """定位并解开 atRestSecretKey（44 字符规范 base64），要求其派生 keyId == target_key_id。

    优先顺序：
      1) 环境变量 WORKBUDDY_ATREST_KEY（直接给密钥串）
      2) 环境变量 WORKBUDDY_ATREST_KEY_FILE（指向密钥文件）
      3) 扫描登录态所在 Data 目录下的 DPAPI blob，逐个解开并校验 keyId
      4) 扫描运行中 WorkBuddy.exe 进程内存（5.6.2 密钥只驻留内存、磁盘无落盘文件的可靠途径）
    返回 atRestSecretKey 字符串；找不到则抛 RuntimeError（带可操作提示）。
    """
    # 1) 直接给密钥
    env_key = os.environ.get("WORKBUDDY_ATREST_KEY")
    if env_key:
        _derive_at_rest_key(env_key)  # 仅校验格式
        return env_key
    # 2) 密钥文件（可能是 DPAPI 密文，也可能是明文 44 字符串）
    env_file = os.environ.get("WORKBUDDY_ATREST_KEY_FILE")
    if env_file and os.path.isfile(env_file):
        try:
            raw = open(env_file, "rb").read()
            return _candidate_to_at_rest_key(raw, target_key_id)
        except Exception:
            pass
    # 3) 扫描 DPAPI blob 自动发现
    roots = _at_rest_scan_roots()
    DPAPI_MAGIC = b"\x01\x00\x00\x00\xd0\x8c\x9d\x0a"
    dpapi_tried = 0
    for root in roots:
        if not os.path.isdir(root):
            continue
        for path, size in _iter_candidate_files(root):
            if size > 256 * 1024:
                continue
            try:
                content = open(path, "rb").read()
            except Exception:
                continue
            idx = content.find(DPAPI_MAGIC)
            if idx < 0:
                continue
            # 从版本字（magic 前 4 字节）到文件尾，作为完整 blob 尝试
            blob = content[idx:]
            dpapi_tried += 1
            try:
                candidate = _candidate_to_at_rest_key(blob, target_key_id)
                if candidate:
                    return candidate
            except Exception:
                continue
    # 4) 扫描运行中客户端进程内存（5.6.2 的 atRestSecretKey 不落盘、只驻留 WorkBuddy.exe 内存）
    try:
        mem_key = _discover_at_rest_key_from_memory(target_key_id)
    except Exception:
        mem_key = None
    if mem_key:
        return mem_key
    raise RuntimeError(
        "无法在本机定位 WorkBuddy 5.6.2+ 的 atRestSecretKey"
        "（已尝试 %d 个 DPAPI blob 与运行中客户端进程内存扫描）。\n"
        "可选项：\n"
        "  (a) 确认 WorkBuddy 客户端已启动并登录（密钥只驻留客户端进程内存，客户端未运行则无法解密），"
        "且本脚本与客户端在同一 Windows 用户下运行；\n"
        "  (b) 复制本机登录态所在 Data 目录下由 WorkBuddy 加密的密钥文件，设置环境变量 "
        "WORKBUDDY_ATREST_KEY_FILE=该文件绝对路径；\n"
        "  (c) 直接设置 WORKBUDDY_ATREST_KEY=<44字符规范base64>；\n"
        "  (d) 退回 WorkBuddy 5.5.x 客户端（该版本登录态为明文，旧脚本即可用）。\n"
        "注：客户端以管理员权限运行而本脚本未提权（或相反）时，进程内存扫描会被系统拒绝。"
        % dpapi_tried)


def _candidate_to_at_rest_key(blob_or_text, target_key_id):
    """尝试把一段字节变成 atRestSecretKey 并校验 keyId。失败抛异常。"""
    # 先尝试作为 DPAPI 密文解开
    text = None
    try:
        plain = _dpapi_unprotect(blob_or_text)
        text = plain.decode("utf-8", "replace").strip()
    except Exception:
        # 也可能就是明文串（无 DPAPI 包裹）
        try:
            text = blob_or_text.decode("utf-8", "replace").strip()
        except Exception:
            text = None
    if not text:
        raise ValueError("无法解析候选密钥")
    # 去掉可能的引号/空白
    text = text.strip().strip('"').strip("'")
    try:
        key, key_id = _derive_at_rest_key(text)
    except Exception:
        raise ValueError("候选不是合法 atRestSecretKey")
    if key_id != target_key_id:
        raise ValueError("keyId 不匹配（%s != %s）" % (key_id, target_key_id))
    return text


def _at_rest_scan_roots():
    roots = []
    home = os.path.expanduser("~")
    for env in ("LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(env, "")
        if base:
            roots.append(os.path.join(base, "CodeBuddyExtension", "Data"))
            roots.append(os.path.join(base, "WorkBuddyExtension", "Data"))
    roots.append(os.path.join(home, ".config", "CodeBuddyExtension", "Data"))
    roots.append(os.path.join(home, ".workbuddy"))
    # 去重保序
    seen = set()
    out = []
    for r in roots:
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


_SKIP_DIRS = {
    "cache", "code cache", "gpucache", "file-tree", "extensions", "blob_storage",
    "indexeddb", "service worker", "logs", "crashpad", "swreporter", "network",
    "application cache", "shadercache", "webrtc", "videoDecodeStats",
}


def _iter_candidate_files(root):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in _SKIP_DIRS]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                sz = os.path.getsize(p)
            except Exception:
                continue
            yield p, sz
            count += 1
            if count > 4000:
                return


def decrypt_access_token_field(raw_value):
    """复刻 ProtectedFieldCodec.decodeString：字符串直接返回；信封则解 AES-GCM。

    raw_value: auth.accessToken 字段（可能是 str，也可能是 {$wbEncrypted:1, envelope}）
    返回明文 JWT 字符串。
    """
    if isinstance(raw_value, str):
        return raw_value  # 明文 JWT（5.5.x 及更早 / 加密关闭时）
    if isinstance(raw_value, dict) and raw_value.get("$wbEncrypted") == 1:
        envelope_b64 = raw_value.get("envelope")
        if not isinstance(envelope_b64, str):
            raise ValueError("加密信封缺少 envelope 字段")
        envelope_bytes = base64.b64decode(envelope_b64)
        if base64.b64encode(envelope_bytes).decode("ascii") != envelope_b64:
            raise ValueError("envelope 不是规范 base64")
        key_id, nonce, ciphertext, auth_tag = _parse_suite1_envelope(envelope_bytes)
        at_rest_secret_key = _discover_at_rest_key(key_id)
        key, _ = _derive_at_rest_key(at_rest_secret_key)
        aad = _build_field_aad(key_id)
        plaintext = _aes_gcm_decrypt(key, nonce, ciphertext, aad, auth_tag)
        return plaintext.decode("utf-8")
    raise ValueError("accessToken 既不是明文也不是加密信封")


def load_token(auth_path):
    with open(auth_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    auth = data.get("auth", {})
    raw = auth.get("accessToken")
    if raw is None:
        raise RuntimeError("登录态文件中未找到 accessToken（可能未登录或登录态已失效）")
    try:
        token = decrypt_access_token_field(raw)
    except Exception as e:
        raise RuntimeError(
            "读取/解密 accessToken 失败（可能为 5.6.2+ 加密登录态）: %s" % e)
    domain = auth.get("domain") or "www.codebuddy.cn"
    _check_expiry(auth)
    return token, domain


def load_token_best():
    """依次尝试所有候选登录态文件，返回 (token, domain, auth_path)。

    对每个候选：
      - 明文 JWT 直接用；
      - 加密信封（5.6.2+）则尝试本地解密（密钥可定位才成功）。
    某候选读取/解密失败（如加密态找不到 atRestSecretKey）则跳过、继续下一个；
    全部失败才汇总报错。这样在「桌面客户端登录态被加密、但 WorkBuddy 代理自带明文
    兜底登录态 ~/.workbuddy/auth 可用」的 5.6.2 场景下，仍能正常签到，
    无需手动设置 WORKBUDDY_ATREST_KEY。
    """
    errors = []
    for p in _auth_candidates():
        if not os.path.isfile(p):
            continue
        try:
            token, domain = load_token(p)
            return token, domain, p
        except Exception as e:
            errors.append((p, str(e)))
            continue
    if not errors:
        raise RuntimeError("未找到本机登录态文件，请确认 WorkBuddy 已登录")
    raise RuntimeError(
        "所有候选登录态文件均无法读取/解密 accessToken（共 %d 个）：\n" % len(errors)
        + "\n".join("  - %s: %s" % (pp, ee) for pp, ee in errors)
        + "\n建议：若均为 5.6.2+ 加密登录态且无法自动定位密钥，请设置 "
          "WORKBUDDY_ATREST_KEY（或 WORKBUDDY_ATREST_KEY_FILE）后重试，"
          "或确保以同一 Windows 用户运行脚本。"
    )


# =============================================================================
# 以下为主流程（与 v3.0.0 保持一致，仅在 load_token 上层兼容 5.6.2 加密）
# =============================================================================

def api_call(base, path, token, payload=None, method="POST"):
    url = base + path
    # GET 且未显式传 payload 时不发请求体；其余情况按 JSON 编码发送
    if method == "GET" and payload is None:
        data = None
    else:
        data = json.dumps(payload if payload is not None else {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "WorkBuddy-Checkin-Script/1.1")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, {"raw": body}
    except urllib.error.HTTPError as e:
        # 非 2xx 也读取响应体（如已签到返回的 HTTP 400 / code=10001）
        try:
            body = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(body)
            except json.JSONDecodeError:
                return e.code, {"raw": body}
        except Exception:
            return e.code, {"raw": ""}


def mask_token(t):
    if not t:
        return "<empty>"
    return t[:6] + "..." + t[-4:]


def _extract_balance(*bodies):
    """从接口响应中尽力提取「积分余额」（total / balance 类字段）。

    不同版本接口返回的余额字段名不统一，这里按候选名 + 嵌套层级兜底提取，
    找不到则返回 None（不影响签到主流程）。
    """
    candidates = (
        "total_credits", "total_credit", "total_credit_balance", "total_points",
        "points_balance", "credit_balance", "balance", "remain_credit",
        "remain_credits", "remain", "score", "credits",
        "integral", "totalCredit", "totalCredits", "pointsBalance",
        "balanceCredit",
    )
    sections = ("", "data", "result", "data.result")
    for body in bodies:
        if not isinstance(body, dict):
            continue
        for sec in sections:
            node = body
            for part in sec.split(".") if sec else []:
                if isinstance(node, dict):
                    node = node.get(part)
                else:
                    node = None
                    break
            if not isinstance(node, dict):
                # 顶层（sec 为空字符串）直接看 body 本身
                node = body if sec == "" else None
            if not isinstance(node, dict):
                continue
            for k in candidates:
                v = node.get(k)
                if isinstance(v, (int, float)):
                    return v
    return None


# ---------------- 消息推送（多渠道，见 push_message.py） ----------------

def load_notify_config():
    """读取本地推送配置（原始结构，可能含新 channels 或旧扁平字段）。

    仅用于判定 enabled / success_notify 等顶层开关；实际渠道解析交给 push_message。
    """
    if not os.path.isfile(NOTIFY_CONFIG):
        return None
    try:
        with open(NOTIFY_CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            return None
        return cfg
    except Exception:
        return None


def _push_failure(res):
    """签到失败时，按本地配置推送提醒；配置缺失或推送模块不可用时静默跳过。

    通过 push_message.send_message 调度多渠道（钉钉/飞书/企业微信/PushPlus/Bark/邮件/…），
    单渠道失败不影响其他渠道，也不影响签到结果与退出码。
    """
    if push_message is None:
        return
    now = time.strftime("%Y-%m-%d %H:%M:%S")  # 本机时区（北京时间）
    msg = res.get("msg", "未知原因")
    auth_file = res.get("detail", {}).get("auth_file", "未知")
    title = "⚠️ WorkBuddy签到助手 · 签到失败"
    content = (
        "### ⚠️ WorkBuddy签到助手 · 签到失败\n\n"
        "> **时间**：%s\n\n"
        "> **原因**：%s\n\n"
        "> **登录态文件**：%s\n\n"
        "> **处理建议**：请检查 WorkBuddy 是否已登录、电脑是否联网、09:00 前后是否开机且客户端未退出；"
        "必要时重启客户端刷新登录态后，可手动再跑一次脚本。\n"
    ) % (now, msg, auth_file)
    try:
        result = push_message.send_message(
            title, content, channels=_PUSH_CHANNELS,
            content_type="markdown", confirm_paid=_PUSH_CONFIRM_PAID)
        res["detail"]["notify"] = result.get("results", [])
    except Exception:
        pass  # 推送失败绝不影响签到


def _push_success(res):
    """签到成功（新签到 / 今日已签跳过）时，按本地配置推送播报。

    仅当 notify_config.json 中 success_notify=true 时才推送；否则静默。
    通过 push_message.send_message 调度多渠道；失败时静默，不影响签到。
    """
    if push_message is None:
        return
    cfg = load_notify_config()
    if not cfg or cfg.get("enabled") is False:
        return
    if not cfg.get("success_notify"):
        return

    now = time.strftime("%Y-%m-%d %H:%M:%S")  # 本机时区（北京时间）
    action = res.get("action")
    # 仅对明确的成功态推送（新签到 / 今日已签跳过）；其他态（失败 / 纯查询）不推
    if action not in ("clicked", "skip_already_signed"):
        return
    msg = res.get("msg", "")
    points = res.get("points")
    streak = res.get("detail", {}).get("streak_days")
    balance = res.get("balance")

    if action == "skip_already_signed":
        title = "✅ WorkBuddy签到助手 · 今日已签"
        content = (
            "### ✅ WorkBuddy签到助手 · 今日已签\n\n"
            "> **时间**：%s\n\n"
            "> **状态**：今日已签到，无需重复领取（幂等保护）\n\n"
            "> **说明**：系统定时任务 / 技能已正常执行，无需处理。\n"
        ) % now
    else:
        title = "✅ WorkBuddy签到助手 · 签到成功"
        lines = (
            "### ✅ WorkBuddy签到助手 · 签到成功\n\n"
            "> **时间**：%s\n\n"
            "> **状态**：%s\n"
        ) % (now, msg)
        if points:
            lines += "> **积分**：+%s\n\n" % points
        if streak:
            lines += "> **连续天数**：第 %s 天\n\n" % streak
        if balance is not None:
            lines += "> **当前积分余额**：%s\n\n" % balance
        lines += "> **说明**：系统定时任务 / 技能已正常执行，无需处理。\n"
        content = lines

    try:
        result = push_message.send_message(
            title, content, channels=_PUSH_CHANNELS,
            content_type="markdown", confirm_paid=_PUSH_CONFIRM_PAID)
        res["detail"]["notify_success"] = result.get("results", [])
    except Exception:
        pass  # 推送失败绝不影响签到


def notify_system(res):
    """签到完成后弹出操作系统级桌面通知（toast / 气球提示），展示结果 + 积分余额。

    跨平台兼容 Windows / macOS / Linux；best-effort、非阻塞、失败静默，
    绝不影响签到结果与退出码。受 --no-notify 一并抑制（与消息推送调试开关一致）。
    """
    try:
        status = res.get("status")
        title = "WorkBuddy签到助手"
        if status == "error":
            body = "签到失败：" + str(res.get("msg", ""))
        else:
            body = str(res.get("msg", "签到完成"))
            bal = res.get("balance")
            if bal is not None:
                body += " ｜ 当前积分余额：" + str(bal)
        _system_toast(title, body)
    except Exception:
        pass  # 通知失败绝不影响签到


def _system_toast(title, body):
    """按操作系统分发到原生命令；任何异常一律忽略。"""
    plat = sys.platform
    try:
        if plat == "darwin":
            msg = body.replace('"', "'")
            subprocess.run(
                ["osascript", "-e",
                 'display notification "%s" with title "%s"' % (msg, title)],
                timeout=5, check=False,
            )
        elif plat.startswith("linux"):
            subprocess.run(["notify-send", title, body], timeout=5, check=False)
        elif plat == "win32":
            _win_toast(title, body)
    except Exception:
        pass


def _win_toast(title, body):
    """Windows：用 .NET 气球提示（非阻塞、约 5s）。PowerShell 不可用或被策略拦截时静默跳过。"""
    safe_title = title.replace("'", "''")
    safe_body = body.replace("'", "''")
    ps = ("Add-Type -AssemblyName System.Windows.Forms;"
          "Add-Type -AssemblyName System.Drawing;"
          "$n=New-Object System.Windows.Forms.NotifyIcon;"
          "$n.Icon=[System.Drawing.SystemIcons]::Information;"
          "$n.Visible=$true;"
          "$n.ShowBalloonTip(5000,'%s','%s','Info');"
          "Start-Sleep -Milliseconds 150;"
          "$n.Dispose()") % (safe_title, safe_body)
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        timeout=10, check=False,
    )


# ---------------- 主流程 ----------------

def _do_checkin(base, token, check_only):
    """执行签到主流程（查询状态 → 必要时领取）。返回结构化结果片段。"""
    result = {"status": "unknown", "action": None, "points": None,
              "balance": None, "msg": "", "detail": {}}
    attempt = 0
    last_err = None
    while attempt <= MAX_RETRY:
        attempt += 1
        try:
            # 1) 查询今日状态
            st_code, st_body = api_call(base, STATUS_PATH, token)
            result["detail"]["status_http"] = st_code
            result["detail"]["status_resp"] = st_body
            # 从状态响应中提前提取积分余额（领取分支会用领取响应再覆盖一次）
            result["balance"] = _extract_balance(st_body)
            result["detail"]["balance"] = result["balance"]

            # 判断是否已签到：兼容多种返回形态
            today_signed = False
            if isinstance(st_body, dict):
                if st_body.get("today_checked_in") is True:
                    today_signed = True
                elif st_body.get("data", {}).get("today_checked_in") is True:
                    today_signed = True
                elif str(st_body.get("code")) == "10001":
                    today_signed = True

            if check_only:
                bal_txt = ("，当前积分余额 %s" % result["balance"]) if result.get("balance") is not None else ""
                result.update(
                    status="ok",
                    action="skip_check_only",
                    msg="状态查询成功（未执行领取）" + bal_txt,
                )
                result["detail"]["today_signed"] = today_signed
                return result

            if today_signed:
                bal_txt = ("，当前积分余额 %s" % result["balance"]) if result.get("balance") is not None else ""
                result.update(status="ok", action="skip_already_signed",
                              msg="今日已签到，无需重复领取" + bal_txt)
                return result

            # 2) 领取签到
            ck_code, ck_body = api_call(base, CHECKIN_PATH, token)
            result["detail"]["checkin_http"] = ck_code
            result["detail"]["checkin_resp"] = ck_body

            if isinstance(ck_body, dict):
                code = str(ck_body.get("code", ""))
                msg = ck_body.get("msg") or ck_body.get("message") or ""
                data = ck_body.get("data") if isinstance(ck_body.get("data"), dict) else {}
                # 已签/重复提示（幂等保护）
                if code == "10001" or "已签到" in msg or "今天已签到" in msg:
                    result.update(status="ok", action="skip_already_signed",
                                  msg="今日已签到（接口返回 code=10001）")
                    return result
                # 领取成功判定：HTTP 2xx 且业务码为成功（兼容无 code / code=0 / code=200）
                success_code = code in ("", "0", "200")
                if 200 <= ck_code < 300 and success_code:
                    credit = (ck_body.get("credit") or data.get("credit")
                              or data.get("daily_credit") or data.get("today_credit"))
                    streak = ck_body.get("streak_days") or data.get("streak_days")
                    # 领取响应若带回余额则覆盖状态响应中的值
                    bbal = _extract_balance(ck_body)
                    if bbal is not None:
                        result["balance"] = bbal
                        result["detail"]["balance"] = bbal
                    bal_txt = ("，当前积分余额 %s" % result["balance"]) if result.get("balance") is not None else ""
                    result.update(status="ok", action="clicked",
                                  points=credit,
                                  msg="领取成功" + (("，+%s 积分" % credit) if credit else "") +
                                      (("，连续第 %s 天" % streak) if streak else "") + bal_txt)
                    result["detail"]["streak_days"] = streak
                    return result
                # 其余视为失败（含 2xx 但业务码异常、或非 2xx）
                result.update(status="error", action="failed",
                              msg=msg or ("HTTP %s（业务码 %s）" % (ck_code, code)))
                return result
            else:
                result.update(status="error", action="failed",
                              msg="领取接口返回非 JSON: %s" % ck_body.get("raw", "")[:200])
                return result

        except urllib.error.HTTPError as e:
            last_err = "HTTP %s: %s" % (e.code, e.reason)
        except urllib.error.URLError as e:
            last_err = "网络错误: %s" % e.reason
        except Exception as e:
            last_err = "异常: %s" % e

        # 重试前稍作等待
        if attempt <= MAX_RETRY:
            time.sleep(2)

    result.update(status="error", msg="重试 %d 次后仍失败: %s" % (MAX_RETRY, last_err))
    return result


# ---------------- 派猫猫旅行 ----------------

def _travel_get(token, path):
    """旅行只读 GET；失败 / 非 200 / 非 0 码均返回 None（静默降级，不影响签到主结果）。"""
    try:
        st, body = api_call(TRAVEL_BASE, path, token, method="GET")
        if st != 200 or not isinstance(body, dict) or body.get("code") != 0:
            return None
        return body.get("data")
    except Exception:
        return None


def _fmt_duration(seconds):
    """把秒数格式化为「X 小时 Y 分」。"""
    if seconds is None:
        return None
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours > 0:
        return "%d 小时 %d 分" % (hours, minutes)
    if minutes > 0:
        return "%d 分钟" % minutes
    return "%d 秒" % seconds


def parse_travel(status, config=None):
    """规整派猫猫旅行状态；字段缺失填 None，绝不编造。"""
    if not isinstance(status, dict):
        return {"available": False}
    locations = []
    if isinstance(config, dict) and isinstance(config.get("locations"), list):
        for loc in config["locations"]:
            if isinstance(loc, dict):
                locations.append({"id": loc.get("id"),
                                  "name": loc.get("name") or "未命名地点"})
    state = status.get("state")
    remaining = None
    if state == "traveling":
        arrive, now = status.get("arrive_at"), status.get("server_now")
        if isinstance(arrive, int) and isinstance(now, int):
            remaining = max(0, arrive - now)
    loc = status.get("location")
    return {
        "available": True,
        "state": state,
        "state_text": TRAVEL_STATE_TEXT.get(state, state),
        "location_name": loc.get("name") if isinstance(loc, dict) else None,
        "reward_credit": status.get("reward_credit"),
        "remaining_seconds": remaining,
        "remaining_text": _fmt_duration(remaining) if remaining is not None else None,
        "daily_limit_reached": status.get("daily_limit_reached"),
        "locations": locations,
    }


def travel_claim(token):
    """领取已到达旅行的积分（写接口）。无可领取时服务端返回非 0 码，如实上报。"""
    try:
        st, body = api_call(TRAVEL_BASE, TRAVEL_CLAIM_PATH, token, payload={})
    except Exception as e:
        return {"action": "failed", "success": False, "message": str(e)}
    if st == 200 and isinstance(body, dict) and body.get("code") == 0:
        data = body.get("data") or {}
        credit = data.get("reward_credit")
        return {"action": "claimed", "success": True, "reward_credit": credit,
                "message": "已领取旅行奖励 +%s 积分" % (credit if credit is not None else "?")}
    return {"action": "failed", "success": False,
            "message": (body.get("msg") if isinstance(body, dict) else None) or ("HTTP %s" % st),
            "code": body.get("code") if isinstance(body, dict) else None}


def travel_depart(token, location_id=None, locations=None):
    """派出 Buddy 旅行（写接口）。location_id 为空时从可选地点中随机。

    仅调用方确认「未达每日上限」后才应调用本函数；本函数不再重复查询状态。
    """
    chosen = location_id
    if chosen is None:
        ids = [loc.get("id") for loc in (locations or []) if loc.get("id") is not None]
        if not ids:
            return {"action": "failed", "success": False,
                    "message": "未能获取可选地点列表，已跳过派遣"}
        chosen = random.choice(ids)
    try:
        st, body = api_call(TRAVEL_BASE, TRAVEL_DEPART_PATH, token,
                            payload={"location_id": chosen})
    except Exception as e:
        return {"action": "failed", "success": False,
                "location_id": chosen, "message": str(e)}
    if st == 200 and isinstance(body, dict) and body.get("code") == 0:
        data = body.get("data") or {}
        loc = data.get("location") or {}
        return {"action": "departed", "success": True, "location_id": chosen,
                "location_name": loc.get("name"), "arrive_at": data.get("arrive_at"),
                "message": "已派出 Buddy 前往【%s】" % (loc.get("name") or ("地点 %s" % chosen))}
    return {"action": "failed", "success": False, "location_id": chosen,
            "message": (body.get("msg") if isinstance(body, dict) else None) or ("HTTP %s" % st),
            "code": body.get("code") if isinstance(body, dict) else None}


def travel_auto(token, location_id=None):
    """派猫猫旅行全自动闭环（写操作）：先领取、后派遣。

    安全约束：派遣前必须先确认 daily_limit_reached 为假，达上限绝不发写请求。
    已到达(arrived) 状态会一直保留、不会丢积分，下次运行自动补领。
    """
    log = []
    status = _travel_get(token, TRAVEL_STATUS_PATH)
    if status is None:
        return {"available": False, "success": False,
                "log": ["查询旅行状态失败，已跳过全部写操作"], "status": None}

    if status.get("state") == "arrived":
        result = travel_claim(token)
        log.append(result.get("message") or result.get("action"))
        if result.get("action") == "claimed":
            fresh = _travel_get(token, TRAVEL_STATUS_PATH)
            if fresh is not None:
                status = fresh

    if status.get("daily_limit_reached"):
        log.append("今日派遣次数已用完，跳过派遣")
    elif status.get("state") in (None, "", "idle"):
        config = _travel_get(token, TRAVEL_CONFIG_PATH) or {}
        result = travel_depart(token, location_id, config.get("locations") or [])
        log.append(result.get("message") or result.get("action"))
    else:
        log.append("Buddy 正在旅行中，无需派遣")

    final = _travel_get(token, TRAVEL_STATUS_PATH) or status
    return {"available": True, "success": True, "log": log, "status": final}


def _run_travel(token, auto=True, location_id=None):
    """收集派猫猫旅行数据；auto 为真时执行全自动闭环（先领后派）。"""
    status = _travel_get(token, TRAVEL_STATUS_PATH)
    config = _travel_get(token, TRAVEL_CONFIG_PATH)
    auto_log = None
    if auto:
        res = travel_auto(token, location_id)
        auto_log = res.get("log") or []
        if res.get("status"):
            status = res["status"]
    parsed = parse_travel(status, config)
    if auto_log is not None:
        parsed["auto_log"] = auto_log
    return parsed


def _append_travel_msg(msg, travel):
    """把旅行状态拼进主消息，便于桌面通知与日志一眼看全。"""
    parts = []
    if travel.get("state_text"):
        parts.append(travel["state_text"])
    if travel.get("location_name"):
        parts.append(travel["location_name"])
    if travel.get("state") == "traveling" and travel.get("remaining_text"):
        parts.append("还需 %s" % travel["remaining_text"])
    if travel.get("state") == "arrived" and travel.get("reward_credit") is not None:
        parts.append("可领 %s 积分" % travel["reward_credit"])
    auto_log = travel.get("auto_log") or []
    if auto_log:
        parts.append("；".join(str(x) for x in auto_log))
    if not parts:
        return msg
    tail = "派猫猫旅行：" + "，".join(parts)
    return (msg + " ｜ " + tail) if msg else tail


def run(check_only=False, do_checkin=True, travel_mode="auto", location_id=None):
    """主流程：登录态 →（可选）签到 →（可选）派猫猫旅行。

    travel_mode：
      "auto"     全自动闭环（先领后派），默认
      "readonly" 仅查询状态，不写
      "off"      跳过旅行
    """
    result = {"status": "unknown", "action": None, "points": None,
              "balance": None, "msg": "", "detail": {}}

    try:
        token, domain, auth_path = load_token_best()
    except Exception as e:
        result.update(status="error", msg="读取登录态失败: %s" % e)
        return result

    base = "https://%s/v2" % domain
    result["detail"]["domain"] = domain
    result["detail"]["auth_file"] = auth_path
    # 仅记录 token 形态，绝不记录真实值
    result["detail"]["token_masked"] = mask_token(token)
    # 标记登录态是否 5.6.2 加密信封（仅记录布尔，不记值）
    try:
        with open(auth_path, "r", encoding="utf-8") as f:
            _ad = json.load(f).get("auth", {}).get("accessToken")
        result["detail"]["token_encrypted"] = bool(
            isinstance(_ad, dict) and _ad.get("$wbEncrypted") == 1)
    except Exception:
        result["detail"]["token_encrypted"] = None

    # ---- 签到 ----
    if do_checkin:
        ck = _do_checkin(base, token, check_only)
        result["status"] = ck.get("status", result["status"])
        result["action"] = ck.get("action")
        result["points"] = ck.get("points")
        result["balance"] = ck.get("balance")
        result["msg"] = ck.get("msg", "")
        result["detail"].update(ck.get("detail", {}))

    # ---- 派猫猫旅行 ----
    if travel_mode != "off":
        travel = _run_travel(token, auto=(travel_mode == "auto"),
                             location_id=location_id)
        result["travel"] = travel
        # 旅行接口不可用时静默降级，绝不改变签到结论
        if travel.get("available"):
            result["msg"] = _append_travel_msg(result.get("msg", ""), travel)
        if not do_checkin:
            # 纯旅行模式：整体状态由旅行结果决定
            result["status"] = "ok" if travel.get("available") else "error"
            result["action"] = "travel"
            if not travel.get("available"):
                result["msg"] = "查询派猫猫旅行状态失败（请确认已登录且网络正常）"

    return result


def write_log(res):
    """把每次运行结果追加写入脚本同目录的 checkin.log（本地核查用，不含 token）。"""
    try:
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkin.log")
        line = "%s | status=%s | action=%s | msg=%s\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"),
            res.get("status"), res.get("action"), res.get("msg"))
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass  # 写日志失败绝不影响签到


# ---------------- 环境自检与配置模板 ----------------

def _has_desktop_session():
    """best-effort 探测当前是否存在桌面图形会话（影响系统通知能否弹出）。

    探测失败或无法确定时返回 "unknown"，绝不影响签到主流程。
    """
    try:
        if sys.platform.startswith("win"):
            # SM_REMOTESESSION：1=远端会话（通常为无桌面/服务态），0=本地控制台
            try:
                import ctypes
                return "yes" if ctypes.windll.user32.GetSystemMetrics(0x1000) == 0 else "remote/no"
            except Exception:
                return "unknown"
        elif sys.platform == "darwin":
            # macOS 桌面环境一般存在；无法可靠区分锁屏，保守返回 yes
            return "yes"
        else:
            # Linux/类 Unix：有 $DISPLAY 或 $WAYLAND_DISPLAY 通常代表有桌面
            if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
                return "yes"
            return "no"
    except Exception:
        return "unknown"


def diagnose():
    """环境自检：检查 Python / 登录态 / 网络 / 桌面会话 / 推送配置。

    只读、不触发任何签到请求，便于用户首次安装后快速确认「能不能用」。
    返回结构化 dict，由 main() 以 JSON 打印。
    """
    report = {"version": VERSION, "python": {}, "auth": {}, "network": {}, "desktop": {}, "notify_config": {}}

    # 1) Python 版本
    report["python"] = {
        "version": sys.version.split()[0],
        "ok": sys.version_info >= (3, 6),
        "note": "" if sys.version_info >= (3, 6) else "低于 3.6，请升级 Python 或改用 WorkBuddy 托管 Python",
    }

    # 2) 登录态文件（枚举所有候选，逐个判定可用性，便于 5.6.2 加密态排错）
    candidates = [p for p in _auth_candidates() if os.path.isfile(p)]
    report["auth"]["candidates"] = []
    chosen = None
    for p in candidates:
        info = {"path": p}
        try:
            with open(p, "r", encoding="utf-8") as _f:
                _ad = json.load(_f).get("auth", {})
            info["encrypted_envelope"] = bool(
                isinstance(_ad.get("accessToken"), dict)
                and _ad.get("accessToken", {}).get("$wbEncrypted") == 1)
            token, domain = load_token(p)
            info["token_present"] = True
            info["domain"] = domain
            info["usable"] = True
            if chosen is None:
                chosen = p
                report["auth"]["found"] = True
                report["auth"]["path"] = p
                report["auth"]["token_present"] = True
                report["auth"]["domain"] = domain
                report["auth"]["expired"] = False
                report["auth"]["encrypted_envelope"] = info["encrypted_envelope"]
        except Exception as e:
            info["token_present"] = False
            info["error"] = str(e)
            info["usable"] = False
            if "过期" in str(e):
                info["expired"] = True
        report["auth"]["candidates"].append(info)
    if chosen is None:
        report["auth"]["found"] = False
        if candidates:
            report["auth"]["hint"] = ("找到登录态文件但均无法读取/解密（5.6.2+ 加密态会自动尝试："
                                      "DPAPI 落盘密钥 → 运行中 WorkBuddy.exe 进程内存扫描；"
                                      "请确保客户端已启动并登录，也可设置 WORKBUDDY_ATREST_KEY）")
        else:
            report["auth"]["hint"] = "未找到登录态文件，请先登录 WorkBuddy 客户端"

    # 3) 网络连通性（DNS 解析 best-effort）
    domain = report["auth"].get("domain") or "www.codebuddy.cn"
    try:
        ip = socket.gethostbyname(domain)
        report["network"]["dns_ok"] = True
        report["network"]["host"] = domain
        report["network"]["resolved_ip"] = ip
    except Exception as e:
        report["network"]["dns_ok"] = False
        report["network"]["host"] = domain
        report["network"]["error"] = str(e)

    # 4) 桌面会话（影响系统通知弹窗）
    report["desktop"]["session"] = _has_desktop_session()
    report["desktop"]["note"] = (
        "存在桌面会话，系统通知可正常弹出" if report["desktop"]["session"] == "yes"
        else "未检测到桌面会话（如锁屏/无 GUI 服务态），系统通知可能不弹出；stdout 与 checkin.log 仍可记录结果"
    )

    # 5) 消息推送配置
    if push_message is not None:
        report["notify_config"] = push_message.ready_channels()
        report["notify_config"]["hint"] = "运行 --init-config 生成模板；--push-channels 可指定本次渠道"
    else:
        cfg = load_notify_config()
        if cfg is None:
            report["notify_config"]["present"] = False
            report["notify_config"]["hint"] = "未配置（可选）；运行 --init-config 生成模板"
        else:
            report["notify_config"]["present"] = True
            report["notify_config"]["enabled"] = cfg.get("enabled", True)
            report["notify_config"]["success_notify"] = bool(cfg.get("success_notify"))
            channels = [k for k in ("wecom_webhook", "pushplus_token", "bark_url") if cfg.get(k)]
            report["notify_config"]["channels"] = channels
            if cfg.get("enabled", True) and not channels:
                report["notify_config"]["warn"] = "enabled=true 但未填写任何通道，推送不会生效"

    return report


USAGE = (
    "WorkBuddy签到助手（接口直签 + 派猫猫旅行）\n\n"
    "用法：\n"
    "  python workbuddy_checkin.py                       # 签到 + 派猫猫旅行全自动闭环（默认）\n"
    "  python workbuddy_checkin.py --no-travel           # 只签到，跳过旅行（最快）\n"
    "  python workbuddy_checkin.py --check-only          # 仅查询（只读，不签到也不写旅行）\n"
    "  python workbuddy_checkin.py travel                # 只查派猫猫旅行状态（只读，不签到）\n"
    "  python workbuddy_checkin.py travel --travel-auto  # 只跑旅行闭环（不签到）\n"
    "  python workbuddy_checkin.py --travel-auto         # 显式开启旅行闭环（默认已开）\n"
    "  python workbuddy_checkin.py --location N          # 指定派遣地点（1-4，缺省随机）\n"
    "  python workbuddy_checkin.py --no-notify           # 跳过全部推送与桌面通知（调试用）\n"
    "  python workbuddy_checkin.py --push-channels dingtalk,email   # 仅向指定渠道推送（覆盖配置）\n"
    "  python workbuddy_checkin.py --confirm-paid        # 允许发送付费渠道（如短信 sms）\n"
    "  python workbuddy_checkin.py --self-test-atrest   # 仅自测 5.6.2 信封解密算法（不联网、不读登录态）\n"
    "  python workbuddy_checkin.py --diagnose            # 环境自检（Python/登录态/网络/桌面会话/推送配置）\n"
    "  python workbuddy_checkin.py --init-config         # 生成 notify_config.json.example 模板\n"
    "  python workbuddy_checkin.py --help                # 显示本帮助\n"
    "  python workbuddy_checkin.py --version             # 显示版本号\n\n"
    "派猫猫旅行说明：\n"
    "  状态三态：idle 空闲 / traveling 旅行中 / arrived 已到达待领取。\n"
    "  闭环顺序为先领取、后派遣；派遣前必查每日上限，达上限绝不发写请求。\n"
    "  已到达不会丢积分，下次运行自动补领。旅行接口不可用时静默降级，不影响签到结论。\n\n"
    "退出码：成功 0 / 失败 1（便于自动化判断是否推送告警）\n"
    "签到成功后会在结果中展示当前积分余额（若接口返回 balance / total_credit 等字段）。\n"
    "【v3.1.0 适配】已兼容 WorkBuddy 5.6.2+ 客户端的登录态 AtRestEncryption：\n"
    "  若 auth.accessToken 变为 AES-256-GCM 信封（{$wbEncrypted:1,envelope}），本脚本会自动解密。\n"
    "  解密密钥自动定位顺序：环境变量 WORKBUDDY_ATREST_KEY / WORKBUDDY_ATREST_KEY_FILE\n"
    "  → DPAPI blob 扫描 → 运行中 WorkBuddy.exe 进程内存扫描（5.6.2 密钥不落盘、\n"
    "  只驻留客户端内存；此路径需客户端已启动登录且与脚本同一 Windows 用户）。\n"
    "消息推送说明：\n"
    "  支持 12 类渠道：dingtalk/feishu/wecom/wechat(pushplus)/email/sms/qq/slack/telegram/bark/webhook/system。\n"
    "  失败自动推送（配置缺失则静默跳过）；成功播报需 success_notify=true。\n"
    "  渠道/凭据见 ~/.workbuddy/scripts/notify_config.json（旧结构微信三字段仍兼容）。\n"
)


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(USAGE)
        sys.exit(0)
    if "--version" in sys.argv:
        print("workbuddy_checkin %s" % VERSION)
        sys.exit(0)
    # 5.6.2 信封解密算法自测（不联网、不读登录态）
    if "--self-test-atrest" in sys.argv:
        ok = _self_test_atrest()
        sys.exit(0 if ok else 1)
    # 环境自检：只读、不签到，便于首次安装后确认可用性
    if "--diagnose" in sys.argv:
        rep = diagnose()
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        sys.exit(0)
    # 生成推送配置模板（可选）
    if "--init-config" in sys.argv:
        if push_message is None:
            print("推送模块缺失，无法生成模板。")
            sys.exit(1)
        p = push_message.write_sample()
        print("已生成配置模板：%s" % p)
        print("复制为 notify_config.json 并填入你的通道密钥（或使用默认值保持关闭）。")
        print("支持渠道见 README『消息推送』章节；旧结构微信三字段仍兼容。")
        sys.exit(0)
    check_only = "--check-only" in sys.argv
    no_notify = "--no-notify" in sys.argv
    travel_only = "travel" in sys.argv[1:]
    travel_auto_flag = "--travel-auto" in sys.argv
    no_travel = "--no-travel" in sys.argv

    # 推送渠道覆盖（可选）：--push-channels dingtalk,email ；缺省用配置里全部已就绪渠道
    global _PUSH_CHANNELS, _PUSH_CONFIRM_PAID
    _PUSH_CHANNELS = None
    _PUSH_CONFIRM_PAID = False
    if "--push-channels" in sys.argv:
        try:
            idx = sys.argv.index("--push-channels")
            _PUSH_CHANNELS = [c.strip() for c in sys.argv[idx + 1].split(",") if c.strip()]
        except (IndexError, ValueError):
            sys.stderr.write("[push] --push-channels 后需跟逗号分隔的渠道名，如 dingtalk,email\n")
    if "--confirm-paid" in sys.argv:
        _PUSH_CONFIRM_PAID = True

    location_id = None
    if "--location" in sys.argv:
        try:
            idx = sys.argv.index("--location")
            location_id = int(sys.argv[idx + 1])
        except (ValueError, IndexError):
            sys.stderr.write("[travel] --location 需为整数（1-4），已忽略\n")

    if travel_only:
        # 纯旅行模式：不签到；默认只读，加 --travel-auto 才跑闭环
        do_checkin = False
        travel_mode = "auto" if travel_auto_flag else "readonly"
    else:
        do_checkin = True
        if no_travel:
            travel_mode = "off"
        elif check_only:
            travel_mode = "readonly"
        else:
            travel_mode = "auto"  # 默认随签到跑全自动闭环

    try:
        res = run(check_only=check_only, do_checkin=do_checkin,
                  travel_mode=travel_mode, location_id=location_id)
    except Exception as e:
        res = {"status": "error", "action": None, "points": None,
               "msg": "脚本未捕获异常: %s" % e, "detail": {}}
    # 输出结果（不含任何真实 token）
    # 失败推送（配置缺失则跳过；--no-notify 用于调试）
    if not no_notify and res.get("status") != "ok":
        try:
            _push_failure(res)
        except Exception:
            pass  # 推送失败不影响签到结果与退出码
    # 成功推送（仅当 notify_config.json 中 success_notify=true；--no-notify 用于调试）
    if not no_notify and res.get("status") == "ok":
        try:
            _push_success(res)
        except Exception:
            pass  # 推送失败不影响签到结果与退出码
    # 系统桌面通知（跨平台 toast / 气球；--no-notify 一并抑制；失败静默）
    if not no_notify:
        try:
            notify_system(res)
        except Exception:
            pass
    # 输出结果（不含任何真实 token）—— 移后以便包含推送状态
    print(json.dumps(res, ensure_ascii=False, indent=2))
    # 本地运行日志（系统定时任务无对话汇报，靠它核查）
    write_log(res)
    # 退出码：成功 0，失败 1，便于自动化判断是否推送告警
    sys.exit(0 if res.get("status") == "ok" else 1)


def _self_test_atrest():
    """自测 5.6.2 信封解密算法：用随机 atRestSecretKey 加密一段样本 JWT，再用读端解密，
    并交叉用 cryptography 与纯 Python 实现互验。全部通过返回 True。"""
    print("== AtRestEncryption 解密自测 ==")
    # 1) 随机 44 字符规范 base64 作为 atRestSecretKey
    raw_key = bytes(range(32))  # 固定更可控；32 字节
    at_rest_secret_key = base64.b64encode(raw_key).decode("ascii")  # 44 字符
    assert len(at_rest_secret_key) == 44
    key, key_id = _derive_at_rest_key(at_rest_secret_key)
    assert _derive_at_rest_key(at_rest_secret_key)[1] == key_id
    print("  [ok] 密钥派生 keyId=%s" % key_id)

    # 2) 构造信封（复刻 AtRestCrypto.seal，framing=field）
    sample_jwt = ("eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ0ZXN0IiwidXNlciI6IlRPVF9BUEki"
                  "fakesignaturepaddingpaddingpaddingpaddingpaddingpadding")
    nonce = os.urandom(12)
    aad = _build_field_aad(key_id)
    ciphertext, auth_tag = _aes_gcm_encrypt(key, nonce, sample_jwt.encode("utf-8"), aad)
    envelope = {
        "suite": 1,
        "keyId": key_id,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "authTag": base64.b64encode(auth_tag).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    envelope_bytes = json.dumps(envelope).encode("utf-8")
    print("  [ok] 构造 suite=1 信封（用 AES-GCM 加密样本）")

    # 3) 读端解析 + 解密
    p_key_id, p_nonce, p_ct, p_tag = _parse_suite1_envelope(envelope_bytes)
    assert p_key_id == key_id
    p_aad = _build_field_aad(p_key_id)
    plaintext = _aes_gcm_decrypt(key, p_nonce, p_ct, p_aad, p_tag)
    assert plaintext.decode("utf-8") == sample_jwt, "解密失败：明文不匹配"
    print("  [ok] 读端解密往返一致（无 DPAPI 部分）")

    # 4) 交叉验证：纯 Python 实现 与 cryptography 实现互验
    pure_dec = _aes_gcm_decrypt_pure(key, p_nonce, p_ct, p_aad, p_tag)
    assert pure_dec.decode("utf-8") == sample_jwt, "纯 Python 解密与样本不一致"
    print("  [ok] 纯 Python AES-GCM 兜底实现 与 主实现 结果一致")

    # 5) 篡改密文 / tag 必须校验失败
    try:
        bad = bytearray(p_ct); bad[0] ^= 0x01
        _aes_gcm_decrypt(key, p_nonce, bytes(bad), p_aad, p_tag)
        print("  [FAIL] 篡改密文未触发校验失败"); return False
    except Exception:
        print("  [ok] 篡改密文被正确拒绝（GCM 完整性校验生效）")
    try:
        bad_tag = bytearray(p_tag); bad_tag[0] ^= 0x01
        _aes_gcm_decrypt(key, p_nonce, p_ct, p_aad, bytes(bad_tag))
        print("  [FAIL] 篡改 authTag 未触发校验失败"); return False
    except Exception:
        print("  [ok] 篡改 authTag 被正确拒绝（GCM 完整性校验生效）")

    # 6) AAD 错一位必须校验失败（验证 AAD 构造关键）
    try:
        bad_aad = bytearray(p_aad); bad_aad[0] ^= 0x01
        _aes_gcm_decrypt(key, p_nonce, p_ct, bytes(bad_aad), p_tag)
        print("  [FAIL] AAD 错误未触发校验失败"); return False
    except Exception:
        print("  [ok] AAD 错误被正确拒绝（证明 AAD 构造被 GCM 严格绑定）")

    print("== 自测全部通过：5.6.2 信封解密算法正确 ==")
    return True


if __name__ == "__main__":
    main()
