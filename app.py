import os
import re
import json
import time
import threading
import concurrent.futures
from datetime import datetime
from flask import Flask, render_template_string, jsonify, request, Response
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

app = Flask(__name__)

# =========================================================
# GLOBAL STATE
# =========================================================
STATE = {
    "online": [],          # list of working proxies
    "scanning": False,
    "fetching": False,
    "total_tested": 0,
    "total_found": 0,
    "total_fetched": 0,
    "scan_thread": None,
    "last_scan": None,
    "log": [],
    "mode": None,
}

LOCK = threading.Lock()

# =========================================================
# PROXY SOURCE URLS
# =========================================================
PROXY_URLS = [
    # ProxyScrape (multiple timeouts)
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=3000&country=all&ssl=all&anonymity=all",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=all",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=50000&country=all&ssl=all&anonymity=all",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=99999&country=all&ssl=all&anonymity=all",
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&protocol=http&timeout=10000",
    "https://api.proxyscrape.com/?request=displayproxies&proxytype=http",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks4&timeout=10000&country=all&ssl=all&anonymity=all",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=10000&country=all&ssl=all&anonymity=all",
    "https://www.proxy-list.download/api/v1/get?type=http",
    "https://www.proxy-list.download/api/v1/get?type=https",
    "https://www.proxy-list.download/api/v1/get?type=socks4",
    "https://www.proxy-list.download/api/v1/get?type=socks5",
    "http://pubproxy.com/api/proxy?limit=50&format=txt&http=true",
    "http://pubproxy.com/api/proxy?limit=50&format=txt&type=socks4",
    "http://pubproxy.com/api/proxy?limit=50&format=txt&type=socks5",

    # JSON APIs
    "https://api.proxifly.dev/proxy?protocol=http&limit=50",
    "https://api.getproxylist.com/proxy?protocol=http",

    # GitHub raw files
    "https://raw.githubusercontent.com/proxy-free/free-proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/proxies.txt",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks4.txt",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    "https://raw.githubusercontent.com/opsxcq/proxy-list/master/list.txt",
    "https://raw.githubusercontent.com/mertguvencli/http-proxy-list/main/proxy-list/data.txt",
    "https://raw.githubusercontent.com/almighty-uncle/Proxy-List/main/HTTP.txt",
    "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/zloi-user/hideip.me/main/http.txt",
    "https://raw.githubusercontent.com/zloi-user/hideip.me/main/https.txt",
    "https://raw.githubusercontent.com/zloi-user/hideip.me/main/socks4.txt",
    "https://raw.githubusercontent.com/zloi-user/hideip.me/main/socks5.txt",

    # HTML pages (parsed with regex)
    "https://free-proxy-list.net/",
    "https://www.sslproxies.org/",
    "https://www.us-proxy.org/",
    "https://www.socks-proxy.net/",
    "https://www.proxy-list.org/download.php?list=proxy",
    "https://spys.one/en/free-proxy-list/",
]

PROXY_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b")

# =========================================================
# HELPERS
# =========================================================
def log(msg):
    with LOCK:
        STATE["log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        if len(STATE["log"]) > 500:
            STATE["log"] = STATE["log"][-500:]


def make_session():
    s = requests.Session()
    retries = Retry(total=1, backoff_factor=0.2,
                    status_forcelist=[500, 502, 503, 504])
    s.mount("http://", HTTPAdapter(max_retries=retries, pool_connections=50, pool_maxsize=50))
    s.mount("https://", HTTPAdapter(max_retries=retries, pool_connections=50, pool_maxsize=50))
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/120.0 Safari/537.36"})
    return s


def extract_proxies(text):
    found = set()
    # Try JSON first
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if "proxy" in data and isinstance(data["proxy"], str):
                found.add(data["proxy"])
            for key in ("data", "proxies"):
                if key in data and isinstance(data[key], list):
                    for item in data[key]:
                        if isinstance(item, str):
                            found.add(item)
                        elif isinstance(item, dict):
                            ip = item.get("ip") or item.get("IP") or item.get("host")
                            port = item.get("port") or item.get("Port")
                            if ip and port:
                                found.add(f"{ip}:{port}")
                            elif "proxy" in item:
                                found.add(str(item["proxy"]))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    found.add(item)
                elif isinstance(item, dict):
                    ip = item.get("ip") or item.get("IP") or item.get("host")
                    port = item.get("port") or item.get("Port")
                    if ip and port:
                        found.add(f"{ip}:{port}")
    except Exception:
        pass

    # Regex scan regardless (catches TXT + HTML)
    for m in PROXY_REGEX.findall(text):
        found.add(m)
    return found


def normalize_proxy(p):
    p = p.strip()
    if not p:
        return None
    # strip protocol prefixes like http://
    p = re.sub(r"^(https?|socks[45])://", "", p, flags=re.I)
    if not PROXY_REGEX.fullmatch(p):
        return None
    ip, port = p.split(":")
    o1, o2, o3, o4 = ip.split(".")
    for o in (o1, o2, o3, o4):
        if not (0 <= int(o) <= 255):
            return None
    p_int = int(port)
    if not (1 <= p_int <= 65535):
        return None
    return p


# =========================================================
# FETCHING
# =========================================================
def fetch_one(session, url, timeout=8):
    try:
        r = session.get(url, timeout=timeout, verify=False)
        if r.status_code == 200 and r.text:
            return extract_proxies(r.text)
    except Exception:
        return set()
    return set()


def fetch_all_proxies():
    log("🌐 Fetching proxy sources (parallel)...")
    STATE["fetching"] = True
    session = make_session()
    all_found = set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=40) as ex:
        futures = {ex.submit(fetch_one, session, u): u for u in PROXY_URLS}
        for fut in concurrent.futures.as_completed(futures):
            try:
                res = fut.result()
                if res:
                    all_found |= res
            except Exception:
                pass

    # Normalize + dedupe
    normalized = set()
    for p in all_found:
        n = normalize_proxy(p)
        if n:
            normalized.add(n)

    STATE["fetching"] = False
    with LOCK:
        STATE["total_fetched"] = len(normalized)
    log(f"✅ Fetched {len(normalized)} unique proxies")
    return list(normalized)


# =========================================================
# TESTING / SCANNING
# =========================================================
TEST_TARGETS = [
    ("http://httpbin.org/ip", 6),
    ("https://httpbin.org/ip", 6),
    ("http://ip-api.com/json", 6),
    ("https://api.ipify.org?format=json", 6),
]


def test_proxy(proxy_str):
    """Return (proxy, ok_bool, response_time)."""
    proxies = {
        "http": f"http://{proxy_str}",
        "https": f"http://{proxy_str}",
    }
    for target, t in TEST_TARGETS:
        try:
            start = time.time()
            r = requests.get(target, proxies=proxies, timeout=t, verify=False)
            elapsed = round((time.time() - start) * 1000)
            if r.status_code == 200 and r.text and len(r.text) > 5:
                return (proxy_str, True, elapsed)
        except Exception:
            continue
    return (proxy_str, False, None)


def scan_worker_pool(proxies, max_workers=5000, source="auto"):
    """Scan proxies in parallel with high concurrency."""
    if not proxies:
        log("⚠️ No proxies to scan")
        return

    STATE["scanning"] = True
    STATE["mode"] = source
    total = len(proxies)
    with LOCK:
        STATE["total_tested"] = 0
        STATE["total_found"] = 0
        if source == "auto":
            STATE["online"] = []

    log(f"🔥 Scanning {total} proxies with up to {max_workers} workers...")

    # requests has GIL bottleneck; use reasonable but high concurrency
    actual_workers = min(max_workers, max(100, total))
    found = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=actual_workers) as ex:
        futures = {ex.submit(test_proxy, p): p for p in proxies}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            done += 1
            try:
                proxy, ok, ms = fut.result()
                if ok:
                    with LOCK:
                        STATE["online"].append({"proxy": proxy, "ms": ms})
                        STATE["total_found"] = len(STATE["online"])
                    found.append(proxy)
            except Exception:
                pass
            if done % 200 == 0 or done == total:
                with LOCK:
                    STATE["total_tested"] = done
                if done % 1000 == 0:
                    log(f"⏳ Progress: {done}/{total} tested, {len(found)} live")

    STATE["scanning"] = False
    STATE["last_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log(f"✅ Scan complete: {len(found)} live proxies from {total} tested")


# =========================================================
# BACKGROUND RUNNERS
# =========================================================
def auto_run():
    try:
        proxies = fetch_all_proxies()
        if not proxies:
            log("❌ No proxies fetched")
            return
        scan_worker_pool(proxies, max_workers=5000, source="auto")
    except Exception as e:
        log(f"❌ Auto run error: {e}")
        STATE["scanning"] = False
        STATE["fetching"] = False


def manual_run(proxies):
    try:
        scan_worker_pool(proxies, max_workers=5000, source="manual")
    except Exception as e:
        log(f"❌ Manual run error: {e}")
        STATE["scanning"] = False


# =========================================================
# ROUTES
# =========================================================
@app.route("/")
def index():
    return render_template_string(HTML_PAGE)


@app.route("/api/start-auto", methods=["POST"])
def start_auto():
    if STATE["scanning"] or STATE["fetching"]:
        return jsonify({"ok": False, "error": "Already running"})
    t = threading.Thread(target=auto_run, daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/start-manual", methods=["POST"])
def start_manual():
    if STATE["scanning"] or STATE["fetching"]:
        return jsonify({"ok": False, "error": "Already running"})

    data = request.get_json(force=True, silent=True) or {}
    raw = data.get("proxies", "")
    if not raw.strip():
        return jsonify({"ok": False, "error": "No proxies provided"})

    lines = re.split(r"[\s,;]+", raw.strip())
    clean = []
    for ln in lines:
        n = normalize_proxy(ln)
        if n:
            clean.append(n)
    clean = list(dict.fromkeys(clean))  # dedupe preserve order

    if not clean:
        return jsonify({"ok": False, "error": "No valid proxies parsed"})

    t = threading.Thread(target=manual_run, args=(clean,), daemon=True)
    t.start()
    return jsonify({"ok": True, "count": len(clean)})


@app.route("/api/status")
def status():
    with LOCK:
        return jsonify({
            "online": STATE["online"],
            "scanning": STATE["scanning"],
            "fetching": STATE["fetching"],
            "total_tested": STATE["total_tested"],
            "total_found": STATE["total_found"],
            "total_fetched": STATE["total_fetched"],
            "last_scan": STATE["last_scan"],
            "mode": STATE["mode"],
            "log": STATE["log"][-60:],
        })


@app.route("/api/clear", methods=["POST"])
def clear():
    if STATE["scanning"]:
        return jsonify({"ok": False, "error": "Scan running"})
    with LOCK:
        STATE["online"] = []
        STATE["total_found"] = 0
        STATE["total_tested"] = 0
        STATE["log"] = []
    return jsonify({"ok": True})


@app.route("/api/export")
def export():
    with LOCK:
        lines = [p["proxy"] for p in STATE["online"]]
    body = "\n".join(lines)
    return Response(
        body,
        mimetype="text/plain",
        headers={"Content-Disposition": "attachment; filename=live_proxies.txt"}
    )


@app.route("/api/export-json")
def export_json():
    with LOCK:
        data = list(STATE["online"])
    body = json.dumps(data, indent=2)
    return Response(
        body,
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=live_proxies.json"}
    )


@app.route("/healthz")
def healthz():
    return "ok"


# =========================================================
# HTML PAGE
# =========================================================
HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>⚡ Ultra Hyper Proxy Scanner</title>
<style>
  :root{
    --bg:#06070d; --bg2:#0c0f18; --card:rgba(18,22,35,.72);
    --border:rgba(120,150,255,.18); --accent:#6c7dff; --accent2:#8b5cf6;
    --success:#22e6a5; --danger:#ff4d6d; --warn:#ffb84d;
    --text:#e6ecff; --muted:#8892b0;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{
    font-family:'Segoe UI',system-ui,-apple-system,sans-serif;
    background: radial-gradient(1200px 800px at 10% 0%, #1a1f3a 0%, transparent 60%),
                radial-gradient(1000px 700px at 90% 100%, #2a0e3d 0%, transparent 60%),
                var(--bg);
    color:var(--text); min-height:100vh; padding:22px;
  }
  .wrap{max-width:1400px;margin:0 auto}
  header{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:22px;flex-wrap:wrap}
  h1{
    font-size:26px;font-weight:800;letter-spacing:.5px;
    background:linear-gradient(90deg,#6c7dff,#8b5cf6,#22e6a5);
    -webkit-background-clip:text;background-clip:text;color:transparent;
    text-shadow:0 0 30px rgba(108,125,255,.2);
  }
  .sub{color:var(--muted);font-size:13px;margin-top:4px}
  .pill{background:rgba(108,125,255,.12);border:1px solid var(--border);padding:6px 12px;border-radius:999px;font-size:12px}

  .grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px}
  @media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}}
  .stat{
    background:var(--card);border:1px solid var(--border);border-radius:16px;
    padding:16px;backdrop-filter:blur(14px);position:relative;overflow:hidden;
  }
  .stat::before{content:"";position:absolute;top:0;left:0;right:0;height:2px;
    background:linear-gradient(90deg,var(--accent),var(--accent2));opacity:.7}
  .stat .lbl{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:1px}
  .stat .val{font-size:28px;font-weight:800;margin-top:6px;
    background:linear-gradient(90deg,#fff,#8b9cff);-webkit-background-clip:text;background-clip:text;color:transparent}
  .stat.green .val{background:linear-gradient(90deg,#fff,#22e6a5);-webkit-background-clip:text;background-clip:text;color:transparent}
  .stat.red .val{background:linear-gradient(90deg,#fff,#ff4d6d);-webkit-background-clip:text;background-clip:text;color:transparent}

  .panel{background:var(--card);border:1px solid var(--border);border-radius:18px;
    padding:18px;backdrop-filter:blur(14px);margin-bottom:18px}
  .panel h2{font-size:15px;margin-bottom:14px;color:#a9b6e0;letter-spacing:1px;text-transform:uppercase}

  .btns{display:flex;gap:10px;flex-wrap:wrap}
  button{
    border:none;border-radius:12px;padding:12px 20px;font-size:14px;font-weight:700;
    cursor:pointer;color:#fff;letter-spacing:.4px;transition:.2s;position:relative;
  }
  button:disabled{opacity:.4;cursor:not-allowed}
  .b-primary{background:linear-gradient(135deg,#6c7dff,#8b5cf6);box-shadow:0 8px 24px rgba(108,125,255,.35)}
  .b-primary:hover:not(:disabled){transform:translateY(-2px);box-shadow:0 12px 28px rgba(108,125,255,.5)}
  .b-green{background:linear-gradient(135deg,#12b981,#22e6a5);box-shadow:0 8px 24px rgba(34,230,165,.35)}
  .b-green:hover:not(:disabled){transform:translateY(-2px)}
  .b-dark{background:rgba(255,255,255,.06);border:1px solid var(--border)}
  .b-dark:hover:not(:disabled){background:rgba(255,255,255,.12)}
  .b-red{background:linear-gradient(135deg,#e11d48,#ff4d6d)}

  textarea{
    width:100%;min-height:130px;background:rgba(0,0,0,.35);
    border:1px solid var(--border);border-radius:12px;padding:12px;
    color:#e6ecff;font-family:'JetBrains Mono',monospace;font-size:13px;resize:vertical;
  }
  textarea::placeholder{color:#4a5578}

  .log{
    background:rgba(0,0,0,.45);border:1px solid var(--border);border-radius:12px;
    padding:12px;font-family:'JetBrains Mono',monospace;font-size:12px;
    height:200px;overflow-y:auto;white-space:pre-wrap;
  }
  .log div{color:#8fa2d0;line-height:1.5}
  .log div:last-child{color:#22e6a5}

  .bar{height:8px;border-radius:8px;background:rgba(255,255,255,.06);overflow:hidden;margin-top:10px}
  .bar > div{height:100%;width:0;background:linear-gradient(90deg,#6c7dff,#8b5cf6,#22e6a5);
    transition:width .3s;box-shadow:0 0 12px rgba(108,125,255,.6)}

  .list{
    background:rgba(0,0,0,.35);border:1px solid var(--border);border-radius:12px;
    max-height:340px;overflow-y:auto;font-family:'JetBrains Mono',monospace;font-size:12.5px;
  }
  .row{display:flex;justify-content:space-between;padding:8px 14px;border-bottom:1px solid rgba(255,255,255,.04)}
  .row:hover{background:rgba(108,125,255,.08)}
  .row .ok{color:#22e6a5;font-weight:700}
  .row .ms{color:#8892b0}
  .empty{padding:24px;text-align:center;color:#4a5578;font-family:'Segoe UI',sans-serif}

  .status{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:13px;color:var(--muted)}
  .dot{width:9px;height:9px;border-radius:50%;background:#4a5578}
  .dot.live{background:#22e6a5;box-shadow:0 0 10px #22e6a5;animation:pulse 1.4s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}

  ::-webkit-scrollbar{width:8px}
  ::-webkit-scrollbar-thumb{background:rgba(108,125,255,.35);border-radius:8px}
</style>
</head>
<body>
<div class="wrap">

  <header>
    <div>
      <h1>⚡ ULTRA HYPER PROXY SCANNER</h1>
      <div class="sub">Auto Fetch • 5000 Workers • Multi-Protocol • Real-time</div>
    </div>
    <div class="status">
      <span class="dot" id="dot"></span>
      <span id="statusText">Idle</span>
      <span class="pill" id="lastScan">Last scan: —</span>
    </div>
  </header>

  <div class="grid">
    <div class="stat"><div class="lbl">Fetched</div><div class="val" id="sFetched">0</div></div>
    <div class="stat"><div class="lbl">Tested</div><div class="val" id="sTested">0</div></div>
    <div class="stat green"><div class="lbl">Online</div><div class="val" id="sOnline">0</div></div>
    <div class="stat red"><div class="lbl">Dead</div><div class="val" id="sDead">0</div></div>
  </div>

  <div class="panel">
    <h2>🚀 Actions</h2>
    <div class="btns">
      <button class="b-primary" id="btnAuto" onclick="startAuto()">⚡ Auto Fetch & Scan</button>
      <button class="b-green" id="btnExport" onclick="exportTxt()">⬇️ Export TXT</button>
      <button class="b-dark" id="btnExportJson" onclick="exportJson()">⬇️ Export JSON</button>
      <button class="b-red" id="btnClear" onclick="clearAll()">🗑️ Clear</button>
    </div>
    <div class="bar"><div id="progBar"></div></div>
  </div>

  <div class="panel">
    <h2>📥 Manual Proxy Import</h2>
    <textarea id="manualInput" placeholder="Paste proxies here — one per line&#10;Example:&#10;1.2.3.4:8080&#10;5.6.7.8:3128&#10;http://9.10.11.12:8000 (protocol stripped)"></textarea>
    <div class="btns" style="margin-top:12px">
      <button class="b-primary" id="btnManual" onclick="startManual()">🔍 Scan Manual Proxies</button>
      <button class="b-dark" onclick="document.getElementById('manualInput').value=''">✖ Clear Input</button>
    </div>
  </div>

  <div class="panel">
    <h2>📡 Live Feed</h2>
    <div class="log" id="logBox"><div>Waiting...</div></div>
  </div>

  <div class="panel">
    <h2>✅ Live Proxies</h2>
    <div class="list" id="listBox"><div class="empty">No live proxies yet</div></div>
  </div>

</div>

<script>
let polling = null;

function fmt(n){ return (n||0).toLocaleString(); }

async function refresh(){
  try{
    const r = await fetch('/api/status');
    const d = await r.json();

    document.getElementById('sFetched').textContent = fmt(d.total_fetched);
    document.getElementById('sTested').textContent  = fmt(d.total_tested);
    document.getElementById('sOnline').textContent  = fmt(d.total_found);
    document.getElementById('sDead').textContent    = fmt(Math.max(0, d.total_tested - d.total_found));

    document.getElementById('lastScan').textContent = 'Last scan: ' + (d.last_scan || '—');

    const dot = document.getElementById('dot');
    const st  = document.getElementById('statusText');
    if(d.fetching){ dot.classList.add('live'); st.textContent='Fetching proxies...'; }
    else if(d.scanning){ dot.classList.add('live'); st.textContent='Scanning ('+d.mode+')...'; }
    else { dot.classList.remove('live'); st.textContent='Idle'; }

    const busy = d.scanning || d.fetching;
    ['btnAuto','btnManual','btnClear'].forEach(id=>document.getElementById(id).disabled = busy);

    const total = d.total_tested + (busy ? 1 : 0);
    const pct   = d.total_tested ? Math.min(100, (d.total_found/d.total_tested)*100) : 0;
    document.getElementById('progBar').style.width = (busy ? Math.max(4, pct) : (d.total_tested?100:0)) + '%';

    const logBox = document.getElementById('logBox');
    if(d.log && d.log.length){
      logBox.innerHTML = d.log.map(l=>`<div>${escapeHtml(l)}</div>`).join('');
      logBox.scrollTop = logBox.scrollHeight;
    }

    const listBox = document.getElementById('listBox');
    if(d.online && d.online.length){
      listBox.innerHTML = d.online
        .slice().reverse()
        .map(p=>`<div class="row"><span class="ok">${escapeHtml(p.proxy)}</span><span class="ms">${p.ms}ms</span></div>`)
        .join('');
    } else {
      listBox.innerHTML = '<div class="empty">No live proxies yet</div>';
    }
  }catch(e){}
}

function escapeHtml(s){return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

async function startAuto(){
  const r = await fetch('/api/start-auto',{method:'POST'});
  const d = await r.json();
  if(!d.ok) alert(d.error||'Failed');
  refresh();
}

async function startManual(){
  const raw = document.getElementById('manualInput').value;
  if(!raw.trim()){ alert('Paste some proxies first'); return; }
  const r = await fetch('/api/start-manual',{
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({proxies: raw})
  });
  const d = await r.json();
  if(!d.ok) alert(d.error||'Failed');
  else alert('Scanning '+d.count+' proxies...');
  refresh();
}

function exportTxt(){ window.location = '/api/export'; }
function exportJson(){ window.location = '/api/export-json'; }

async function clearAll(){
  if(!confirm('Clear all results and logs?')) return;
  await fetch('/api/clear',{method:'POST'});
  refresh();
}

refresh();
polling = setInterval(refresh, 1500);
</script>
</body>
</html>
"""

# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)
