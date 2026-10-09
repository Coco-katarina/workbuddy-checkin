#!/usr/bin/env node
/**
 * WorkBuddy 每日签到 - 令牌读取脚本 v2.0（跨平台：macOS / Windows / Linux）
 *
 * 读取策略（按优先级）：
 *   A. 新版明文登录态（旧版桌面端）：auth.accessToken 为明文字符串，纯 Node 直读。
 *   B. 新版加密登录态（v5.3.8+ 桌面端）：auth.accessToken 为 $wbEncrypted 信封
 *      （AES-256-GCM，密钥 = sha256(atRestSecretKey)）。atRestSecretKey 由桌面端
 *      原生模块在进程内持有（workbuddyStorage.loggerGet()），不落盘，因此：
 *        B1. 命中本地令牌缓存（28 天有效期内）→ 直接使用，不重启应用；
 *        B2. 已有 --inspect 调试端口 → 经 CDP 读取密钥，本地解密；
 *        B3. 都没有 → 短暂重启 WorkBuddy（带 --inspect，约 30 秒）读取密钥，
 *            完成后自动恢复正常启动。整个过程全自动，聊天记录在云端不受影响。
 *   C. 旧版 state.vscdb（老桌面端）：回退 Electron safeStorage 解密。
 *
 * 安全警示（务必阅读）：
 *   - 本脚本输出的 accessToken 等同 WorkBuddy 账号密码，属于高敏感凭据。
 *   - token 仅通过 stdout 的 DECRYPT_RESULT:<token> 首行输出，由调用方经管道立即消费；
 *     切勿 tee/重定向到文件、切勿粘贴分享、切勿提交到任何仓库。
 *   - 缓存文件（--refresh-cache / 默认缓存）仅写入用户目录，权限 600，可用
 *     WB_CHECKIN_CACHE=0 关闭。缓存的是 accessToken 本身，请知悉。
 *   - 日志只记录签到结果，绝不记录 token 原文。
 *
 * 运行方式：
 *   node decrypt-token.js                    # 输出 DECRYPT_RESULT:<accessToken> 等行
 *   node decrypt-token.js --refresh-cache    # 仅刷新本地缓存，不在 stdout 输出 token
 *   （旧版 state.vscdb 分支需要用 Electron 运行本脚本）
 *
 * 输出：stdout 首行 DECRYPT_RESULT:<accessToken>；失败输出 DECRYPT_RESULT:ERR ...
 *   随后追加账号字段（供 checkin 脚本拼装鉴权头，对齐桌面端 buildHeaders）：
 *     ACCOUNT_UID:<account.uid>
 *     AUTH_DOMAIN:<auth.domain>
 *     ENTERPRISE_ID:<account.enterpriseId>
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");
const http = require("http");
const { spawn, execFile } = require("child_process");

const REFRESH_ONLY = process.argv.includes("--refresh-cache");

// Electron 仅旧版 state.vscdb 分支需要；try/catch 使脚本可在纯 node 下执行。
let app = null;
let safeStorage = null;
try {
  const electron = require("electron");
  app = electron.app || null;
  safeStorage = electron.safeStorage || null;
} catch (e) {
  // 纯 Node 运行（无 Electron）：仅 A/B 分支可用，旧版 state.vscdb 分支自动禁用。
}

const APP_NAME = process.env.WB_CHECKIN_APP_NAME || "WorkBuddy";
const INSPECT_PORT = Number(process.env.WB_CHECKIN_INSPECT_PORT || 9339);
const CACHE_ENABLED = process.env.WB_CHECKIN_CACHE !== "0";
const CACHE_FILE = path.join(os.homedir(), ".workbuddy", "tools", "checkin-token-cache.json");

// 统一输出：先 flush stdout，延迟退出。
function emitAndExit(code, line) {
  process.stdout.write(line + "\n");
  const exitFn = app ? () => app.exit(code) : () => process.exit(code);
  setTimeout(exitFn, 200);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ============================================================
// 路径
// ============================================================

// 新版明文/加密认证文件候选路径（按平台）
function desktopAuthFileCandidates() {
  const home = os.homedir();
  const ap = process.env.APPDATA || "";
  const xdg = process.env.XDG_CONFIG_HOME || path.join(home, ".config");
  const rel = path.join("CodeBuddyExtension", "Data", "Public", "auth", "workbuddy-desktop.info");
  if (process.platform === "darwin") {
    return [path.join(home, "Library", "Application Support", rel)];
  }
  if (process.platform === "win32") {
    const localAp = process.env.LOCALAPPDATA || "";
    return [path.join(localAp, rel), path.join(ap, rel)];
  }
  return [path.join(xdg, rel)];
}

// 旧版 state.vscdb 会话库候选路径
function legacyVscdbCandidates() {
  const home = os.homedir();
  const ap = process.env.APPDATA || "";
  const xdg = process.env.XDG_CONFIG_HOME || path.join(home, ".config");
  const apps = ["WorkBuddy", "CodeBuddy"];
  const roots =
    process.platform === "darwin"
      ? apps.map((a) => path.join(home, "Library", "Application Support", a))
      : process.platform === "win32"
        ? apps.map((a) => path.join(ap, a))
        : apps.map((a) => path.join(xdg, a));
  return roots.map((r) => path.join(r, "User", "globalStorage", "state.vscdb"));
}

// ============================================================
// B 分支：加密信封解密（AES-256-GCM，逆向自客户端 crypto 模块）
// ============================================================

// AAD 构造（sym-v1 + field framing + suite 1）：
//   "WB-AAD\0" + [0x01] + u32(len)"WBEV1" + u32(len)"sym-v1"
//   + u32(suite=1) + u32(len)keyIdHex + [framing=2] + [0x00](无sequence) + [0x00](无final)
function buildAad(keyIdHex) {
  const u32 = (n) => { const b = Buffer.alloc(4); b.writeUInt32BE(n); return b; };
  const st = (s) => { const b = Buffer.from(s, "utf8"); return Buffer.concat([u32(b.length), b]); };
  return Buffer.concat([
    Buffer.from("WB-AAD\0", "ascii"),
    Buffer.from([1]),
    st("WBEV1"),
    st("sym-v1"),
    u32(1),
    st(keyIdHex),
    Buffer.from([2]),
    Buffer.from([0]),
    Buffer.from([0]),
  ]);
}

function keyIdOf(keyBuf) {
  return crypto.createHash("sha256").update(keyBuf).digest("hex").slice(0, 16);
}

function decryptEnvelope(envelopeObj, keyBuf) {
  const d = crypto.createDecipheriv(
    "aes-256-gcm",
    keyBuf,
    Buffer.from(envelopeObj.nonce, "base64"),
    { authTagLength: 16 }
  );
  d.setAAD(buildAad(envelopeObj.keyId));
  d.setAuthTag(Buffer.from(envelopeObj.authTag, "base64"));
  return Buffer.concat([
    d.update(Buffer.from(envelopeObj.ciphertext, "base64")),
    d.final(),
  ]).toString("utf8");
}

// ---------- CDP：从运行中的客户端读取 atRestSecretKey ----------

function cdpTargets(port, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: "127.0.0.1", port, path: "/json/list", timeout: timeoutMs }, (res) => {
      let data = "";
      res.on("data", (c) => (data += c));
      res.on("end", () => {
        try { resolve(JSON.parse(data)); } catch (e) { reject(e); }
      });
    });
    req.on("timeout", () => { req.destroy(); reject(new Error("timeout")); });
    req.on("error", reject);
  });
}

function cdpEvaluate(wsUrl, expr, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const done = (fn, v) => { if (!settled) { settled = true; clearTimeout(timer); fn(v); } };
    const timer = setTimeout(() => { try { ws.close(); } catch {} done(reject, new Error("evaluate 超时")); }, timeoutMs);
    let ws;
    try { ws = new WebSocket(wsUrl); } catch (e) { return done(reject, e); }
    ws.onopen = () => ws.send(JSON.stringify({ id: 1, method: "Runtime.evaluate", params: { expression: expr, returnByValue: true } }));
    ws.onmessage = (ev) => {
      try {
        const m = JSON.parse(ev.data);
        if (m.id === 1) {
          try { ws.close(); } catch {}
          if (m.result && m.result.exceptionDetails) return done(reject, new Error(JSON.stringify(m.result.exceptionDetails).slice(0, 400)));
          done(resolve, m.result && m.result.result && m.result.result.value);
        }
      } catch (e) { done(reject, e); }
    };
    ws.onerror = () => done(reject, new Error("ws 错误"));
  });
}

// 在客户端进程内取 workbuddyStorage.loggerGet()（含 atRestSecretKey）
const CDP_EXPR = `(() => {
  const tried = []; let el = null;
  try { el = process.getBuiltinModule('electron'); if (el) tried.push('builtin'); } catch (e) {}
  if (!el) { try { el = (process.mainModule && process.mainModule.require) ? process.mainModule.require('electron') : null; if (el) tried.push('mainModule'); } catch (e) {} }
  if (!el) { try {
    const path = require('path');
    const base = process.platform === 'darwin'
      ? process.execPath.replace('/Contents/MacOS/' + path.basename(process.execPath), '/Resources/app.asar/package.json')
      : path.join(path.dirname(process.execPath), 'resources', 'app.asar', 'package.json');
    el = require('module').createRequire(base)('electron'); if (el) tried.push('createRequire');
  } catch (e) { tried.push('cr:' + e.message); } }
  if (!el) return JSON.stringify({ err: 'no-electron', tried });
  const ws = el.workbuddyStorage || (el.default && el.default.workbuddyStorage);
  if (!ws) return JSON.stringify({ err: 'no-binding', keys: Object.keys(el).slice(0, 40) });
  try { return JSON.stringify({ ok: true, data: ws.loggerGet() }); }
  catch (e) { return JSON.stringify({ err: 'loggerGet:' + e.message }); }
})()`;

async function cdpWsUrls(port) {
  const urls = [];
  // 标准发现：/json/list（多数 Node/Electron 均可用）
  try {
    const res = await fetch(`http://127.0.0.1:${port}/json/list`);
    if (res.ok) {
      const l = await res.json();
      if (Array.isArray(l)) for (const t of l) if (t.webSocketDebuggerUrl) urls.push(t.webSocketDebuggerUrl);
    }
  } catch {}
  if (urls.length) return urls;
  // 兜底：WorkBuddy 桌面端（Electron 37）主进程 inspector 的 /json/list 会异常断连，
  // 但启动日志会打印 "Debugger listening on ws://127.0.0.1:<port>/<uuid>"，直接解析拿 WS URL
  const logFile = process.env.WB_CHECKIN_INSPECT_LOG || path.join(os.tmpdir(), "wb-checkin-inspect.log");
  try {
    const txt = fs.readFileSync(logFile, "utf8");
    const all = [...txt.matchAll(new RegExp(`ws://127\\.0\\.0\\.1:${port}/[0-9a-f-]+`, "g"))];
    if (all.length) urls.push(all[all.length - 1][0]); // 取最近一次
  } catch {}
  return urls;
}

async function extractSecretViaCdp(port) {
  const urls = await cdpWsUrls(port);
  if (!urls.length) return null;
  for (const wsUrl of urls) {
    try {
      let v = await cdpEvaluate(wsUrl, CDP_EXPR);
      if (typeof v === "string" && v.trim().startsWith("{")) v = JSON.parse(v);
      if (v && v.ok && v.data && v.data.atRestSecretKey) return String(v.data.atRestSecretKey);
    } catch (e) { /* 尝试下一个 URL */ }
  }
  return null;
}

// ---------- 应用进程控制（macOS / Windows / Linux） ----------

function appBinCandidates() {
  if (process.env.WB_CHECKIN_APP_BIN) return [process.env.WB_CHECKIN_APP_BIN];
  const home = os.homedir();
  if (process.platform === "darwin") {
    return ["/Applications/WorkBuddy.app/Contents/MacOS/Electron", "/Applications/CodeBuddy.app/Contents/MacOS/Electron"];
  }
  if (process.platform === "win32") {
    const la = process.env.LOCALAPPDATA || "", pf = process.env["ProgramFiles"] || "C:\\Program Files", pf86 = process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)";
    return [
      path.join(la, "Programs", "WorkBuddy", "WorkBuddy.exe"),
      path.join(la, "Programs", "workbuddy", "WorkBuddy.exe"),
      path.join(la, "WorkBuddy", "WorkBuddy.exe"),
      path.join(pf, "WorkBuddy", "WorkBuddy.exe"),
      path.join(pf86, "WorkBuddy", "WorkBuddy.exe"),
    ];
  }
  const xdg = process.env.XDG_CONFIG_HOME || path.join(home, ".config");
  return [
    path.join(xdg, "WorkBuddy", "WorkBuddy"),
    "/opt/WorkBuddy/workbuddy",
    "/usr/share/workbuddy/workbuddy",
  ];
}

function findAppBin() {
  for (const c of appBinCandidates()) {
    try { if (c && fs.existsSync(c)) return c; } catch {}
  }
  return null;
}

function execIgnore(cmd, args) {
  try { spawn(cmd, args, { stdio: "ignore", detached: true }).unref(); } catch {}
}

function quitApp() {
  try {
    if (process.platform === "darwin") {
      try { execFile("osascript", ["-e", `quit app "${APP_NAME}"`], () => {}); } catch {}
      // 兜底：在沙箱/无辅助功能权限环境下 osascript 会被拒绝（错误 -10004），
      // 此时改从 sandbox-center socket 文件名解析主进程 PID 直接强杀
      setTimeout(() => {
        const pid = findMainPidViaSocket();
        if (pid) { try { process.kill(pid, "SIGKILL"); } catch {} }
      }, 3000);
    } else if (process.platform === "win32") {
      execIgnore("taskkill", ["/IM", `${APP_NAME}.exe`, "/F"]);
    } else {
      execIgnore("pkill", ["-f", APP_NAME]);
    }
  } catch {}
}

// WorkBuddy 主进程 PID 不易直接定位，但 sandbox-center 的 socket 文件名携带其父 PID：
// /tmp/workbuddy-sandbox-center-<hex>-<mainPid>.sock
// 注意：socket 文件可能是陈旧残留（客户端重启后旧文件未清理），必须校验
// 1) mtime 足够新（10 分钟内） 2) PID 仍然存活，二者同时满足才允许强杀
function findMainPidViaSocket() {
  try {
    const tmp = os.tmpdir();
    const now = Date.now();
    for (const f of fs.readdirSync(tmp)) {
      const m = f.match(/^workbuddy-sandbox-center-[0-9a-f]+-(\d+)\.sock$/);
      if (!m) continue;
      const full = path.join(tmp, f);
      let mtime = 0;
      try { mtime = fs.statSync(full).mtimeMs; } catch { continue; }
      if (now - mtime > 10 * 60 * 1000) continue; // 陈旧 socket，跳过
      const pid = Number(m[1]);
      try { process.kill(pid, 0); } catch { continue; } // PID 已死，跳过
      return pid;
    }
  } catch {}
  return null;
}

function cleanAppEnv() {
  // 关键：沙箱环境常设 ELECTRON_RUN_AS_NODE=1，若被客户端继承会导致其以纯 Node
  // 模式启动并立即退出（表现为 CDP 永远连不上）。启动客户端前必须剥离。
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.NODE_OPTIONS;
  return env;
}

function launchApp(bin, withInspect) {
  const args = withInspect ? [`--inspect=${INSPECT_PORT}`] : [];
  const env = cleanAppEnv();
  try {
    if (process.platform === "darwin" && !withInspect && bin.endsWith("/Electron")) {
      // 正常启动走 open -a，让系统按常规方式拉起
      execIgnore("open", ["-a", APP_NAME]);
    } else if (process.platform === "win32" && !withInspect) {
      execIgnore("cmd.exe", ["/c", "start", "", bin]);
    } else {
      // 调试实例：输出必须落盘（"Debugger listening on ws://..." 是 CDP 兜底发现的来源）
      const logPath = process.env.WB_CHECKIN_INSPECT_LOG || path.join(os.tmpdir(), "wb-checkin-inspect.log");
      const out = withInspect ? fs.openSync(logPath, "w") : "ignore";
      spawn(bin, args, { stdio: ["ignore", out, out], detached: true, env }).unref();
    }
  } catch {}
}

// 读取 atRestSecretKey：已有调试端口则直接用，否则自动重启客户端获取
async function getAtRestSecretKey() {
  // 关键：调试实例启动后 /json/list 需要数秒才会出现 target，必须轮询而非单次探测，
  // 否则会误判「CDP 不可用」而触发不必要的重启分支
  let key = null;
  for (let i = 0; i < 20 && !key; i++) {
    key = await extractSecretViaCdp(INSPECT_PORT);
    if (!key) await sleep(1500);
  }
  if (key) return { key, restarted: false };

  const bin = findAppBin();
  if (!bin) {
    throw new Error(
      "未找到 WorkBuddy 客户端可执行文件（可用环境变量 WB_CHECKIN_APP_BIN 指定路径）。" +
      "或已手动开启调试端口时设置 WB_CHECKIN_INSPECT_PORT。"
    );
  }
  process.stderr.write(
    "[提示] 本地登录态为加密存储，需要短暂重启 WorkBuddy 客户端读取解密密钥（约 30 秒，完成后自动恢复）...\n"
  );
  quitApp();
  await sleep(5000);
  launchApp(bin, true);
  const deadline = Date.now() + 40000;
  while (Date.now() < deadline) {
    await sleep(1500);
    key = await extractSecretViaCdp(INSPECT_PORT);
    if (key) break;
  }
  quitApp();
  await sleep(8000); // 留足时间让 quitApp 的兜底强杀（3s）先完成，再正常拉起
  launchApp(bin, false);
  if (!key) throw new Error("重启客户端后仍未能读取解密密钥（CDP 提取失败）");
  return { key, restarted: true };
}

// ---------- 本地令牌缓存（默认开启，WB_CHECKIN_CACHE=0 关闭） ----------

function readCache() {
  if (!CACHE_ENABLED) return null;
  try {
    const j = JSON.parse(fs.readFileSync(CACHE_FILE, "utf8"));
    if (j && j.token && j.expiresAt && Date.now() < j.expiresAt - 5 * 60 * 1000) return j;
  } catch {}
  return null;
}

function writeCache(rec) {
  if (!CACHE_ENABLED) return;
  try {
    fs.mkdirSync(path.dirname(CACHE_FILE), { recursive: true });
    fs.writeFileSync(CACHE_FILE, JSON.stringify(rec, null, 2), { mode: 0o600 });
    try { fs.chmodSync(CACHE_FILE, 0o600); } catch {}
  } catch {}
}

// ============================================================
// C 分支：旧版 state.vscdb + Electron safeStorage
// ============================================================

const SESSION_KEYS = [
  'secret://{"extensionId":"tencent-cloud.coding-copilot","key":"planning-genie.new.accessTokencn"}',
];

function readValue(dbPath, key) {
  try {
    const { DatabaseSync } = require("node:sqlite");
    const db = new DatabaseSync(dbPath, { readOnly: true });
    const row = db.prepare("SELECT value FROM ItemTable WHERE key = ?").get(key);
    db.close();
    return row ? row.value : null;
  } catch (e) {
    if (process.env.WB_CHECKIN_ALLOW_PY_FALLBACK !== "1") {
      throw new Error("无法用 node:sqlite 读取会话数据库，且未开启 python3 回退（WB_CHECKIN_ALLOW_PY_FALLBACK=1）");
    }
    const { execFileSync } = require("child_process");
    const script =
      "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);r=c.execute('SELECT value FROM ItemTable WHERE key=?',(sys.argv[2],)).fetchone();print(json.dumps(r[0]) if r else '')";
    const out = execFileSync("python3", ["-c", script, dbPath, key], { encoding: "utf8", timeout: 15000 }).trim();
    return out ? JSON.parse(out) : null;
  }
}

function toBuffer(parsed) {
  if (parsed && parsed.type === "Buffer" && Array.isArray(parsed.data)) return Buffer.from(parsed.data);
  if (typeof parsed === "string") return Buffer.from(parsed, "base64");
  if (Buffer.isBuffer(parsed)) return parsed;
  return null;
}

// ============================================================
// 主流程
// ============================================================

function accountLines(j) {
  const acct = j && j.account;
  const authObj = j && j.auth;
  const uid = acct && acct.uid != null ? String(acct.uid) : "";
  const domain = authObj && authObj.domain != null ? String(authObj.domain) : "";
  const eid = acct && acct.enterpriseId != null ? String(acct.enterpriseId) : "";
  return `\nACCOUNT_UID:${uid}\nAUTH_DOMAIN:${domain}\nENTERPRISE_ID:${eid}`;
}

async function main() {
  // 1. 新版认证文件（明文或加密信封）
  for (const f of desktopAuthFileCandidates()) {
    if (!fs.existsSync(f)) continue;
    let j = null;
    try { j = JSON.parse(fs.readFileSync(f, "utf8")); } catch { continue; }
    const tokenField = j && j.auth && j.auth.accessToken;
    if (!tokenField) continue;

    // A. 明文字符串（旧版桌面端）
    if (typeof tokenField === "string") {
      process.stderr.write("[安全提示] 已读取明文 accessToken，仅用于 WorkBuddy 官方签到接口；请勿写入日志、分享或提交。\n");
      emitAndExit(0, "DECRYPT_RESULT:" + tokenField + accountLines(j));
      return;
    }

    // B. $wbEncrypted 加密信封（v5.3.8+ 桌面端）
    if (tokenField && typeof tokenField === "object" && tokenField.$wbEncrypted === 1 && typeof tokenField.envelope === "string") {
      // B1. 缓存命中
      const cached = readCache();
      if (cached) {
        process.stderr.write("[提示] 使用本地缓存的 accessToken（" + CACHE_FILE + "，未过期）。\n");
        emitAndExit(0, `DECRYPT_RESULT:${cached.token}\nACCOUNT_UID:${cached.uid || ""}\nAUTH_DOMAIN:${cached.domain || ""}\nENTERPRISE_ID:${cached.enterpriseId || ""}`);
        return;
      }

      // B2/B3. CDP 取密钥（必要时自动重启客户端）
      let secret;
      try {
        secret = await getAtRestSecretKey();
      } catch (e) {
        emitAndExit(7, "DECRYPT_RESULT:ERR " + e.message);
        return;
      }
      const keyBuf = crypto.createHash("sha256").update(Buffer.from(secret, "utf8")).digest();
      let envelope;
      try { envelope = JSON.parse(Buffer.from(tokenField.envelope, "base64").toString("utf8")); } catch (e) {
        emitAndExit(7, "DECRYPT_RESULT:ERR 信封解析失败: " + e.message);
        return;
      }
      if (keyIdOf(keyBuf) !== envelope.keyId) {
        emitAndExit(7, "DECRYPT_RESULT:ERR 密钥校验失败（keyId 不匹配，客户端可能刚刷新了密钥，请重试一次）");
        return;
      }
      let token;
      try { token = decryptEnvelope(envelope, keyBuf); } catch (e) {
        emitAndExit(7, "DECRYPT_RESULT:ERR 信封解密失败: " + e.message);
        return;
      }

      // 刷新整个文件里的信封解密结果缓存（accessToken + 过期时间 + 账号字段）
      const expiresAt = (j.auth && j.auth.expiresAt) || Date.now() + 7 * 24 * 3600 * 1000;
      const acct = j.account || {};
      writeCache({
        version: 1,
        token,
        uid: acct.uid != null ? String(acct.uid) : "",
        domain: (j.auth && j.auth.domain) || "",
        enterpriseId: acct.enterpriseId != null ? String(acct.enterpriseId) : "",
        expiresAt,
        savedAt: Date.now(),
      });
      process.stderr.write("[安全提示] 已解密 accessToken 并写入本地缓存（权限 600，WB_CHECKIN_CACHE=0 可关闭）；请勿分享或提交。\n");
      if (REFRESH_ONLY) { emitAndExit(0, "DECRYPT_RESULT:OK refreshed"); return; }
      emitAndExit(0, "DECRYPT_RESULT:" + token + accountLines(j));
      return;
    }
  }

  // 2. 回退：旧版 state.vscdb + Electron safeStorage
  if (!app || !safeStorage) {
    emitAndExit(
      6,
      "DECRYPT_RESULT:ERR 未找到可用的本地登录态（新版认证文件缺失），且当前为纯 Node 运行（无 Electron）无法解密旧版 state.vscdb。" +
      "请确认已安装并登录 WorkBuddy 桌面端。"
    );
    return;
  }
  app.setName(APP_NAME);
  let raw = null;
  let legacyReadErr = null;
  for (const p of legacyVscdbCandidates()) {
    if (!fs.existsSync(p)) continue;
    for (const k of SESSION_KEYS) {
      try {
        const v = readValue(p, k);
        if (v) { raw = v; break; }
      } catch (e) { legacyReadErr = e; }
    }
    if (raw) break;
  }
  if (!raw) {
    const hint = legacyReadErr ? "（读取旧版 state.vscdb 失败：" + legacyReadErr.message + "）" : "";
    emitAndExit(2, "DECRYPT_RESULT:ERR 未找到 WorkBuddy 本地登录态（请先安装并登录 WorkBuddy 桌面端）" + hint);
    return;
  }
  app.whenReady().then(() => {
    if (!safeStorage.isEncryptionAvailable()) {
      emitAndExit(3, "DECRYPT_RESULT:ERR 系统加密不可用");
      return;
    }
    try {
      const buf = toBuffer(JSON.parse(raw));
      if (!buf) throw new Error("未知的存储格式");
      const decrypted = safeStorage.decryptString(buf);
      const session = JSON.parse(decrypted);
      const token = session && session.auth && session.auth.accessToken;
      if (token) {
        process.stderr.write("[安全提示] 已从旧版会话解密 accessToken，仅用于官方签到接口。\n");
        emitAndExit(0, "DECRYPT_RESULT:" + token + accountLines(session));
        return;
      }
      emitAndExit(4, "DECRYPT_RESULT:ERR 会话中无 accessToken");
    } catch (e) {
      emitAndExit(4, "DECRYPT_RESULT:ERR 解密失败(" + e.message + ")。旧版应用可尝试设置 WB_CHECKIN_APP_NAME=CodeBuddy 后重试");
    }
  });
}

main().catch((e) => {
  emitAndExit(9, "DECRYPT_RESULT:ERR " + (e && e.message ? e.message : String(e)));
});
