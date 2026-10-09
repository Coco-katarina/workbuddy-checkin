/*
 * WorkBuddy签到助手 · 网页授权码工具（v3.1.7）
 * ---------------------------------------------------------------------------
 * 用途：在你「已登录」的浏览器页面上运行一次，把该会话的凭据（连同原始
 *       User-Agent 与站点域名）打包成一段授权码复制到剪贴板，交给签到助手保存。
 *       授权码自带 User-Agent，因此不受官方网关「会话与 UA 绑定」影响。
 *
 * 安全边界（本工具只做一件事）：
 *   - 只读取当前页面自身的会话数据（页面 Cookie / 页面存储中的令牌）
 *   - 只把结果写入剪贴板，**不向任何服务器发送数据**、不读取本机任何文件
 *   - 不使用任何外部依赖，不上报、不埋点
 *
 * 使用位置：https://www.workbuddy.cn （已登录，与客户端同一账号）
 *
 * 说明：若你的页面会话 Cookie 标记为 HttpOnly，页面脚本读不到它，
 *       本工具会提示改用「开发者工具 -> Copy as cURL」或导出 HAR 的方式授权。
 */

/* ======================= 复制以下内容 ======================= */

【方式A · Console（推荐）】在开发者工具 Console 中整段粘贴回车：

(()=>{const S=[localStorage,sessionStorage],g=k=>{for(const s of S){try{const v=s.getItem(k);if(v)return v}catch(e){}}return""};let mode="",val="";const keys=["accessToken","access_token","token","jwt","authToken","Authorization"];for(const k of keys){const v=g(k);if(v&&/^(eyJ|Bearer\s)/i.test(v.trim())){mode="bearer";val=v.trim().replace(/^Bearer\s+/i,"");break}}if(!mode){const c=document.cookie||"";if(/session=/.test(c)){mode="cookie";val=c}}if(!mode){alert("未取到可用会话。\n若页面会话 Cookie 为 HttpOnly，请在该请求上右键 -> Copy -> Copy as cURL，改用 cURL 方式授权。");return}const payload={v:1,mode:mode,value:val,ua:navigator.userAgent,domain:location.origin};let b64="";try{const u=new TextEncoder().encode(JSON.stringify(payload));b64=btoa(String.fromCharCode.apply(null,u))}catch(e){b64=btoa(unescape(encodeURIComponent(JSON.stringify(payload))))}const code="WBAUTH1:"+b64.replace(/=+$/,"").replace(/\+/g,"-").replace(/\//g,"_");const done=()=>alert("已复制授权码（"+code.length+" 字符，模式 "+mode+"）。\n请回到对话，把它粘贴给 WorkBuddy 签到助手。");try{navigator.clipboard.writeText(code).then(done,()=>prompt("复制失败，请手动复制以下授权码：",code))}catch(e){prompt("复制失败，请手动复制以下授权码：",code)}})();

【方式B · 一键书签】把下面这一整行存为浏览器书签（名称随意，如「WB授权码」），
在已登录页面点一下即可：

javascript:(()=>{const S=[localStorage,sessionStorage],g=k=>{for(const s of S){try{const v=s.getItem(k);if(v)return v}catch(e){}}return""};let mode="",val="";const keys=["accessToken","access_token","token","jwt","authToken","Authorization"];for(const k of keys){const v=g(k);if(v&&/^(eyJ|Bearer\s)/i.test(v.trim())){mode="bearer";val=v.trim().replace(/^Bearer\s+/i,"");break}}if(!mode){const c=document.cookie||"";if(/session=/.test(c)){mode="cookie";val=c}}if(!mode){alert("未取到可用会话。\n若页面会话 Cookie 为 HttpOnly，请在该请求上右键 -> Copy -> Copy as cURL，改用 cURL 方式授权。");return}const payload={v:1,mode:mode,value:val,ua:navigator.userAgent,domain:location.origin};let b64="";try{const u=new TextEncoder().encode(JSON.stringify(payload));b64=btoa(String.fromCharCode.apply(null,u))}catch(e){b64=btoa(unescape(encodeURIComponent(JSON.stringify(payload))))}const code="WBAUTH1:"+b64.replace(/=+$/,"").replace(/\+/g,"-").replace(/\//g,"_");const done=()=>alert("已复制授权码（"+code.length+" 字符，模式 "+mode+"）。\n请回到对话，把它粘贴给 WorkBuddy 签到助手。");try{navigator.clipboard.writeText(code).then(done,()=>prompt("复制失败，请手动复制以下授权码：",code))}catch(e){prompt("复制失败，请手动复制以下授权码：",code)}})();

/* ======================= 复制到此结束 ======================= */

/*
 * 拿到授权码后，交给助手保存（只需一次）：
 *     python scripts/web_auth.py --setup-code '<授权码>'
 * 之后即可长期自动签到：
 *     python scripts/auto_checkin.py
 */
