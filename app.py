import asyncio
import os
import re
import json
import time
import random
import threading
import aiohttp
from flask import Flask, jsonify, request, render_template_string
from telethon import TelegramClient, events
from telethon.sessions import StringSession

app = Flask(__name__)

# ================= CONFIG =================
API_ID = int(os.environ.get("API_ID", 23941289))
API_HASH = os.environ.get("API_HASH", "591fff002ba2c3b3f6abda1f92d73330")
SESSION_STRING = os.environ.get("SESSION_STRING", "1BJWap1sBu6zJEgjKWRCn_IyZnLH18wsnENZzd8pwpXBHUJ9I67SWlM8KqzRwh-C6fKSq5vULcuGWx1QUe29n-OX7pJ9ASWFY1O9FqiDO4Z0_3yp0ygr8x8Oqh5B-pXolK5idfo9LHb4VPQXC0dcTRiZKziXdSSvepeBJDY2W94neZXznsWhT6GpUC8vcAhn2-D49X5vVQYc3vvLth3laS3Buxl0q3U0ZzUYi6M7Ocp4sb6XRXaDpr-wvnlTmmwaat2g0OhlOkLzrPsdJGLmi6KKzdx6OvAUx_KHyWeOH8eNbTdH3BVkW7VCSzHDVM1SbLDZbKy_iByzB5ilO9Xg5G5h_XesZcUA=")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "@QXAOSINTBOT")
GROUP_ID_RAW = os.environ.get("GROUP_ID", "-1003736621633")

try:
    GROUP_ID = int(GROUP_ID_RAW)
except ValueError:
    GROUP_ID = None

client = None
telethon_loop = None
active_proxy = None

# ================= PROXY =================
PROXY_SOURCES = [
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&protocol=http&timeout=10000",
    "https://api.proxyscrape.com/?request=displayproxies&proxytype=http",
    "https://www.proxy-list.download/api/v1/get?type=http",
    "http://pubproxy.com/api/proxy?limit=50&format=txt&http=true",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    "https://raw.githubusercontent.com/opsxcq/proxy-list/master/list.txt",
    "https://raw.githubusercontent.com/almighty-uncle/Proxy-List/main/HTTP.txt",
    "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
]


async def fetch_source(session, url, result_set):
    try:
        timeout = aiohttp.ClientTimeout(total=4)
        async with session.get(url, timeout=timeout, ssl=False,
                               headers={"User-Agent": "Mozilla/5.0"}) as resp:
            if resp.status != 200:
                return
            text = await resp.text(errors='ignore')
            if '<html' in text.lower() or '<body' in text.lower():
                for m in re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}:\d+\b', text):
                    result_set.add(m)
            else:
                if text.strip().startswith('{') or text.strip().startswith('['):
                    try:
                        data = json.loads(text)
                        if isinstance(data, list):
                            for item in data:
                                if isinstance(item, str):
                                    result_set.add(item)
                        elif isinstance(data, dict):
                            for key in ['proxy', 'proxies', 'data']:
                                if key in data:
                                    if isinstance(data[key], str):
                                        result_set.add(data[key])
                                    elif isinstance(data[key], list):
                                        for x in data[key]:
                                            if isinstance(x, str):
                                                result_set.add(x)
                    except:
                        pass
                else:
                    for line in text.split('\n'):
                        line = line.strip()
                        if ':' in line:
                            result_set.add(line)
    except:
        pass


async def test_single_proxy(session, proxy):
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        connector = aiohttp.TCPConnector(ssl=False)
        async with session.get(
            "https://api.telegram.org",
            proxy=f"http://{proxy}",
            timeout=timeout,
            connector=connector
        ) as resp:
            return resp.status < 500
    except:
        return False


async def fetch_and_test_proxies():
    global active_proxy
    print("🔍 Fetching live proxies...")
    raw_proxies = set()
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_source(session, url, raw_proxies) for url in PROXY_SOURCES]
        await asyncio.gather(*tasks, return_exceptions=True)

    valid = []
    for p in raw_proxies:
        p = p.strip()
        match = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)$', p)
        if match:
            valid.append(f"{match.group(1)}:{match.group(2)}")
    valid = list(set(valid))
    print(f"📦 Total unique proxies: {len(valid)}")

    if not valid:
        return None

    async with aiohttp.ClientSession() as session:
        chunk = valid[:100]
        results = await asyncio.gather(*[test_single_proxy(session, p) for p in chunk], return_exceptions=True)
        working = [p for p, ok in zip(chunk, results) if ok is True]

    if working:
        active_proxy = random.choice(working)
        print(f"✅ Working proxy: {active_proxy}")
        return active_proxy
    print("❌ No working proxy found, connecting direct...")
    return None


# ================= RESULT DETECTION =================
STRICT_RESULT_MARKERS = [
    "QXA OSINT RESULT",
    "Powered By QXA",
    "Powered By QXA TEAM",
    "USER DETAILS",
    "Telegram ID  :",
    "Telegram ID :",
]

PROCESSING_MARKERS = [
    "⏳", "Processing...", "Processing", "Fetching",
    "Please wait", "Please Wait", "Searching", "Loading", "⏱",
]


def is_processing_message(text: str) -> bool:
    if not text:
        return True
    if any(m in text for m in STRICT_RESULT_MARKERS):
        return False
    first_line = text.strip().split("\n", 1)[0]
    if any(m in first_line for m in PROCESSING_MARKERS):
        return True
    if len(text) < 200 and any(m in text for m in PROCESSING_MARKERS):
        return True
    return False


def is_final_result(text: str) -> bool:
    if not text:
        return False
    if any(m in text for m in STRICT_RESULT_MARKERS):
        return True
    if len(text) > 200 and not is_processing_message(text):
        if "Usage:" in text and len(text) < 400:
            return False
        if "will self-destruct" in text and len(text) < 400:
            return False
        if "invalid" in text.lower() and len(text) < 400:
            return False
        return True
    return False


# ================= FETCH (FIXED) =================
async def fetch_bot_data(full_command, target_chat, timeout=90):
    try:
        if not client.is_connected():
            await client.connect()

        try:
            entity = await client.get_entity(target_chat)
        except Exception as e:
            print(f"❌ Cannot resolve '{target_chat}': {e}")
            return {"error": f"Cannot resolve chat '{target_chat}' - {e}"}

        chat_id = entity.id
        entity_id = entity.id
        print(f"✅ Resolved '{target_chat}' -> id={chat_id}")

        final_text = None
        collected = []
        send_time = [None]

        @client.on(events.NewMessage())
        async def handler(event):
            nonlocal final_text
            if event.message.out:
                return
            text = event.message.text or ""
            if not text:
                return

            chat_match = (event.chat_id == chat_id) or (event.chat_id == -chat_id)
            sender_match = False
            try:
                sender = await event.get_sender()
                if sender and getattr(sender, "id", None) == entity_id:
                    sender_match = True
            except Exception:
                pass

            if not (chat_match or sender_match):
                return

            if send_time[0] and event.message.date:
                try:
                    if event.message.date.timestamp() < send_time[0] - 2:
                        return
                except Exception:
                    pass

            preview = text[:120].replace("\n", " ")
            print(f"📩 Incoming (id={event.message.id}, len={len(text)}): {preview}")
            collected.append((len(text), text))

            if is_final_result(text):
                final_text = text
                print(f"✅ Final captured ({len(text)} chars)")
            elif is_processing_message(text):
                print("⏳ Skipped processing")

        await asyncio.sleep(0.5)

        sent_msg = await client.send_message(entity, full_command)
        try:
            send_time[0] = sent_msg.date.timestamp()
        except Exception:
            send_time[0] = time.time()
        print(f"📤 Sent '{full_command}' (msg_id={sent_msg.id})")

        start_time = time.time()
        last_log = -1
        while time.time() - start_time < timeout:
            if final_text:
                break
            elapsed = int(time.time() - start_time)
            if elapsed > 0 and elapsed % 10 == 0 and elapsed != last_log:
                print(f"⏱️  Waiting... {elapsed}s / {timeout}s")
                last_log = elapsed
            await asyncio.sleep(0.4)

        client.remove_event_handler(handler)

        if final_text:
            return {"text": final_text, "success": True}
        if collected:
            collected.sort(key=lambda x: x[0], reverse=True)
            best = collected[0][1]
            print(f"⚠️ Timeout, returning best ({len(best)} chars)")
            return {"text": best, "success": True, "fallback": True}
        return {"error": f"Timeout: No final result in {timeout}s"}

    except Exception as e:
        print(f"❌ fetch_bot_data error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


# ================= JSON PARSER =================
def clean_val(v):
    if not v:
        return None
    v = v.strip()
    # strip emojis/flag at end
    v = re.sub(r'[\U0001F1E6-\U0001F1FF\u2600-\u27BF\uD83C-\uDBFF\uDC00-\uDFFF]+$', '', v).strip()
    if v.lower() in ("not available", "n/a", "none", "null", "-", ""):
        return None
    return v


def parse_osint_result(raw_text: str) -> dict:
    """
    Saara data structured JSON mein organize karo.
    """
    data = {
        "raw": raw_text,
        "success": True,
        "source": "QXA OSINT",
        "sections": {
            "telegram": {},
            "identity": {},
            "contact": {},
            "documents": {},
            "location": {},
            "social": {},
            "finance": {},
            "vehicle": {},
            "other": {}
        }
    }

    if not raw_text:
        data["success"] = False
        return data

    # ---------- Individual field regex ----------
    fields = {
        # Telegram
        "telegram_id":   (r'(?:Telegram\s*ID|ᴛᴇʟᴇɢraᴍ\s*ɪᴅ)\s*:?\s*(\d+)', "telegram"),
        "username":      (r'Username\s*:?\s*@?([A-Za-z0-9_]{3,})', "telegram"),
        "tg_name":       (r'Telegram\s*Name\s*:?\s*([^\n]+)', "telegram"),

        # Identity
        "name":          (r'(?<!Telegram )(?<!Father )Name\s*:?\s*([^\n]+)', "identity"),
        "father_name":   (r'(?:Father(?:\'s)?\s*Name|पिता)\s*:?\s*([^\n]+)', "identity"),
        "mother_name":   (r'(?:Mother(?:\'s)?\s*Name|माता)\s*:?\s*([^\n]+)', "identity"),
        "gender":        (r'Gender\s*:?\s*([^\n]+)', "identity"),
        "dob":           (r'(?:DOB|Date\s*of\s*Birth)\s*:?\s*([^\n]+)', "identity"),
        "age":           (r'Age\s*:?\s*(\d+)', "identity"),

        # Contact
        "mobile":        (r'(?:Mobile|Phone|Contact)\s*:?\s*(\+?[\d\s\(\)\-]{8,})', "contact"),
        "alt_mobile":    (r'(?:Alternate|Alt)\s*(?:Mobile|Phone)\s*:?\s*(\+?[\d\s\(\)\-]{8,})', "contact"),
        "email":         (r'Email\s*:?\s*([^\s\n]+@[^\s\n]+)', "contact"),
        "country":       (r'Country\s*:?\s*([^\n]+)', "contact"),
        "state":         (r'State\s*:?\s*([^\n]+)', "contact"),
        "city":          (r'City\s*:?\s*([^\n]+)', "contact"),
        "pincode":       (r'(?:Pin\s*code|Pincode|PIN)\s*:?\s*(\d{6})', "contact"),

        # Documents
        "aadhaar":       (r'Aadhaar(?:\s*Number)?\s*:?\s*(\d{12})', "documents"),
        "pan":           (r'PAN(?:\s*Number)?\s*:?\s*([A-Z]{5}\d{4}[A-Z])', "documents"),
        "voter_id":      (r'(?:Voter|EPIC)\s*(?:ID)?\s*:?\s*([A-Z]{3}\d{7})', "documents"),
        "dl":            (r'(?:DL|Driving\s*License)\s*:?\s*([A-Z0-9\-]{10,20})', "documents"),
        "passport":      (r'Passport\s*:?\s*([A-Z][0-9]{7})', "documents"),

        # Location
        "address":       (r'Address\s*:?\s*([^\n]+(?:\n[^\n]+)*?)(?=\n[━━🗄🔥]|\n[A-Z][a-z]+\s*:|$)', "location"),
        "district":      (r'District\s*:?\s*([^\n]+)', "location"),

        # Social
        "instagram":     (r'Instagram\s*:?\s*@?([A-Za-z0-9_.]{3,})', "social"),
        "facebook":      (r'Facebook\s*:?\s*@?([A-Za-z0-9_.]{3,})', "social"),
        "twitter":       (r'(?:Twitter|X)\s*:?\s*@?([A-Za-z0-9_]{3,})', "social"),
        "github":        (r'GitHub\s*:?\s*@?([A-Za-z0-9\-]{3,})', "social"),
        "snapchat":      (r'Snapchat\s*:?\s*@?([A-Za-z0-9_.]{3,})', "social"),

        # Finance
        "upi":           (r'UPI(?:\s*ID)?\s*:?\s*([^\s\n]+@[^\s\n]+)', "finance"),
        "bank":          (r'Bank(?:\s*Name)?\s*:?\s*([^\n]+)', "finance"),
        "ifsc":          (r'IFSC\s*:?\s*([A-Z]{4}0[A-Z0-9]{6})', "finance"),
        "account":       (r'(?:Account|A/C)\s*(?:Number)?\s*:?\s*(\d{9,18})', "finance"),

        # Vehicle
        "vehicle":       (r'(?:Vehicle|RC)\s*(?:Number)?\s*:?\s*([A-Z]{2}\d{1,2}[A-Z]{1,2}\d{4})', "vehicle"),
        "vehicle_model": (r'Vehicle\s*Model\s*:?\s*([^\n]+)', "vehicle"),

        # Other / source
        "source":        (r'(?:Source)\s*:?\s*([^\n]+)', "other"),
        "ip":            (r'\b((?:\d{1,3}\.){3}\d{1,3})\b', "other"),
    }

    for key, (pat, section) in fields.items():
        m = re.search(pat, raw_text, re.IGNORECASE)
        if m:
            val = clean_val(m.group(1))
            if val:
                data["sections"][section][key] = val

    # ---------- Embedded JSON extraction ----------
    # Try to find any JSON blob inside raw (bot might return JSON)
    json_match = re.search(r'\{[\s\S]{20,}\}', raw_text)
    if json_match:
        try:
            embedded = json.loads(json_match.group(0))
            data["embedded_json"] = embedded
        except Exception:
            pass

    # ---------- Auto-classify remaining lines ----------
    # Any "Key: Value" line not already parsed → "other" section
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        # Skip separator lines
        if re.match(r'^[━─=\-_]{3,}$', line):
            continue
        # "Key : Value" pattern
        m = re.match(r'^([A-Za-z][A-Za-z0-9 _/\-]{1,40})\s*:\s*(.+)$', line)
        if not m:
            continue
        k = m.group(1).strip().lower().replace(" ", "_")
        v = clean_val(m.group(2))
        if not v:
            continue
        # Skip if key already exists somewhere in sections
        already = False
        for sec in data["sections"].values():
            if k in sec:
                already = True
                break
        if not already:
            data["sections"]["other"][k] = v

    # ---------- Remove empty sections ----------
    data["sections"] = {k: v for k, v in data["sections"].items() if v}

    # ---------- Flat view ----------
    flat = {}
    for sec_name, sec_data in data["sections"].items():
        for k, v in sec_data.items():
            flat[k] = v
    data["flat"] = flat

    return data


# ================= HTML =================
HOMEPAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QXA OSINT API</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Tahoma,sans-serif}
body{background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);color:#e2e8f0;min-height:100vh;padding:20px}
.container{max-width:1100px;margin:0 auto}
header{text-align:center;padding:40px 20px 30px;border-bottom:1px solid #334155;margin-bottom:30px}
header h1{font-size:2.5rem;background:linear-gradient(90deg,#38bdf8,#818cf8,#c084fc);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:10px}
header p{color:#94a3b8}
.status{display:inline-block;margin-top:15px;padding:6px 16px;border-radius:20px;background:#064e3b;color:#6ee7b7;font-size:0.85rem;border:1px solid #10b981}
h2{color:#38bdf8;margin:30px 0 15px;font-size:1.4rem;border-left:4px solid #38bdf8;padding-left:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:15px}
.card{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:18px;transition:all 0.3s;cursor:pointer}
.card:hover{border-color:#38bdf8;transform:translateY(-3px);box-shadow:0 8px 20px rgba(56,189,248,0.15)}
.card h3{color:#f1f5f9;font-size:1rem;margin-bottom:8px}
.card .endpoint{color:#94a3b8;font-size:0.8rem;word-break:break-all;font-family:'Courier New',monospace;background:#0f172a;padding:6px 8px;border-radius:5px;margin-bottom:8px}
.card .example{color:#6ee7b7;font-size:0.78rem;font-family:'Courier New',monospace;background:#064e3b;padding:8px 10px;border-radius:5px;overflow-x:auto;white-space:pre;line-height:1.5}
.tag{display:inline-block;font-size:0.7rem;padding:2px 8px;border-radius:10px;background:#3730a3;color:#c7d2fe;margin-bottom:6px}
footer{text-align:center;padding:30px 0;color:#64748b;font-size:0.85rem;border-top:1px solid #334155;margin-top:40px}
.note{background:#422006;border:1px solid #f59e0b;color:#fbbf24;padding:12px 16px;border-radius:8px;margin:15px 0;font-size:0.85rem}
</style>
</head>
<body>
<div class="container">
<header>
<h1>🔍 QXA OSINT API</h1>
<p>Powered By QXA TEAM</p>
<div class="status">● API Online</div>
</header>
<div class="note">
💡 <b>Tip:</b> Add <code>&amp;hidden=true</code> to hide raw + only structured data.
</div>
<h2>📋 Endpoints</h2>
<div class="grid" id="endpoints"></div>
<footer><p>Base URL: <code id="baseurl"></code></p></footer>
</div>
<script>
document.getElementById('baseurl').textContent = window.location.origin;
const endpoints = [
{tag:"Phone",title:"Number Lookup",example:"/num=9093614290"},
{tag:"Telegram",title:"Telegram Lookup",example:"/tg=6858648491"},
{tag:"Aadhaar",title:"Aadhaar Lookup",example:"/aadhaar=672459048432"},
{tag:"Aadhaar",title:"Aadhaar Family",example:"/adharfamily=672459048432"},
{tag:"Email",title:"Email Lookup",example:"/email=test@gmail.com"},
{tag:"Name",title:"Name Lookup",example:"/name=Rahul"},
{tag:"Search",title:"Advanced Search",example:"/advanced=Rahul"},
{tag:"Leak",title:"Leak Info",example:"/leak=test@gmail.com"},
{tag:"UPI",title:"UPI Verify",example:"/upi=9593964430@ibl"},
{tag:"Bank",title:"IFSC Lookup",example:"/ifsc=SBIN0001234"},
{tag:"Tax",title:"PAN to GST",example:"/pantogst=CFXPV5799D"},
{tag:"Location",title:"Pincode",example:"/pincode=110001"},
{tag:"Network",title:"IP Lookup",example:"/ip=8.8.8.8"},
{tag:"Vehicle",title:"Vehicle Lookup",example:"/vehicle=MH47CD9112"},
{tag:"Game",title:"Free Fire",example:"/ff=123456789"},
{tag:"Game",title:"BGMI",example:"/bgmi=123456789"},
{tag:"Social",title:"Instagram",example:"/insta=rahul"},
{tag:"Social",title:"GitHub",example:"/github=torvalds"},
{tag:"Social",title:"Snapchat",example:"/snap=rahul"},
{tag:"Payment",title:"PayTM Info",example:"/paytm=9093614290"},
{tag:"Phone",title:"Basic Info",example:"/basic=9093614290"},
{tag:"Device",title:"IMEI Info",example:"/imei=123456789012345"},
{tag:"Telegram",title:"Telegram ID Info",example:"/tgid=6858648491"},
{tag:"SMS",title:"SMS Bomber",example:"/bomb=9093614290"},
{tag:"Pakistan",title:"Pakistan SIM",example:"/pk=923001234567"},
{tag:"Stats",title:"Your Stats",example:"/stats"}
];
const grid = document.getElementById('endpoints');
endpoints.forEach(e => {
  const card = document.createElement('div');
  card.className = 'card';
  card.innerHTML = `<div class="tag">${e.tag}</div><h3>${e.title}</h3><div class="endpoint">${e.example.split('=')[0]}=<value></div><div class="example">GET ${e.example}</div>`;
  card.onclick = () => {
    navigator.clipboard.writeText(window.location.origin + e.example);
    card.style.borderColor = '#10b981';
    setTimeout(()=>card.style.borderColor='', 600);
  };
  grid.appendChild(card);
});
</script>
</body>
</html>
"""


# ================= ROUTES =================
@app.route('/')
def home():
    return render_template_string(HOMEPAGE)


@app.route('/<path:full_path>')
def dynamic_command(full_path):
    valid_commands = [
        'num', 'tg', 'aadhaar', 'adharfamily', 'email', 'name',
        'advanced', 'leak', 'upi', 'ifsc', 'pantogst', 'pincode',
        'ip', 'vehicle', 'ff', 'bgmi', 'insta', 'github', 'snap',
        'paytm', 'basic', 'imei', 'tgid', 'bomb', 'pk', 'stats'
    ]

    hidden_only = request.args.get('hidden', 'false').lower() in ('true', '1', 'yes')
    target_chat = BOT_USERNAME
    command_path = full_path

    if full_path.startswith("group&"):
        target_chat = GROUP_ID
        command_path = full_path.replace("group&", "", 1)
    elif full_path.startswith("bot&"):
        target_chat = BOT_USERNAME
        command_path = full_path.replace("bot&", "", 1)

    if command_path == "stats":
        command, value = "stats", ""
    else:
        if "=" not in command_path:
            return jsonify({"success": False, "error": "Invalid format. Use /command=value"}), 400
        command, value = command_path.split("=", 1)

    if command not in valid_commands:
        return jsonify({
            "success": False,
            "error": f"Invalid command '{command}'",
            "available": valid_commands
        }), 400

    if telethon_loop is None or not telethon_loop.is_running():
        return jsonify({"success": False, "error": "Telethon not connected"}), 500

    full_command = f"/{command} {value}".strip()

    future = asyncio.run_coroutine_threadsafe(
        fetch_bot_data(full_command, target_chat, timeout=90),
        telethon_loop
    )
    try:
        fetched = future.result(timeout=100)
    except TimeoutError:
        return jsonify({"success": False, "error": "Request Timeout"}), 504
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    if "error" in fetched:
        return jsonify({
            "success": False,
            "command": full_command,
            "error": fetched["error"]
        }), 400

    raw_result = fetched["text"]
    parsed = parse_osint_result(raw_result)

    # ---------- Base response ----------
    response = {
        "success": True,
        "command": full_command,
        "target": "Group" if target_chat == GROUP_ID else "Bot",
        "fallback_used": fetched.get("fallback", False),
        "data": parsed["sections"],   # nested structured data
        "flat": parsed["flat"],       # flat key->value map
    }

    if not hidden_only:
        response["raw"] = raw_result
        if "embedded_json" in parsed:
            response["embedded_json"] = parsed["embedded_json"]

    return jsonify(response)


@app.route('/health')
def health():
    return jsonify({
        "success": True,
        "status": "ok",
        "bot": BOT_USERNAME,
        "telethon_connected": telethon_loop is not None and telethon_loop.is_running(),
        "active_proxy": active_proxy
    })


@app.route('/debug_tg=<path:target>')
def debug_tg(target):
    if not target.startswith('@') and not target.isdigit():
        target = '@' + target
    if telethon_loop is None or not telethon_loop.is_running():
        return jsonify({"success": False, "error": "Telethon not running"}), 500
    future = asyncio.run_coroutine_threadsafe(_debug_fetch(target), telethon_loop)
    try:
        return jsonify(future.result(timeout=90))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


async def _debug_fetch(target):
    logs = []
    try:
        if not client.is_connected():
            await client.connect()
        try:
            bot_entity = await client.get_entity(BOT_USERNAME)
            logs.append({"step": "resolve", "ok": True, "id": bot_entity.id})
        except Exception as e:
            logs.append({"step": "resolve", "ok": False, "error": str(e)})
            return {"success": False, "logs": logs}

        sent = await client.send_message(bot_entity, f"/tg {target}")
        logs.append({"step": "send", "ok": True, "msg_id": sent.id})

        received = []
        start = time.time()

        @client.on(events.NewMessage())
        async def handler(event):
            text = event.message.text or ""
            if event.message.out or not text:
                return
            try:
                sender = await event.get_sender()
                if not sender or getattr(sender, "id", None) != bot_entity.id:
                    return
            except Exception:
                return
            received.append({
                "id": event.message.id,
                "len": len(text),
                "preview": text[:300],
                "is_processing": is_processing_message(text),
                "is_final": is_final_result(text)
            })

        while time.time() - start < 60:
            await asyncio.sleep(0.5)

        client.remove_event_handler(handler)
        return {"success": True, "logs": logs, "received": received, "count": len(received)}
    except Exception as e:
        return {"success": False, "logs": logs, "error": str(e)}


# ================= TELETHON THREAD =================
def start_telethon():
    global telethon_loop, client
    telethon_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(telethon_loop)

    proxy_str = telethon_loop.run_until_complete(fetch_and_test_proxies())

    if proxy_str:
        from python_socks.async_.asyncio import Proxy
        host, port = proxy_str.split(':')

        async def connector(ip, tport):
            proxy = Proxy.from_url(f"http://{host}:{port}")
            sock = await proxy.connect(dest_host=ip, dest_port=tport)
            return await asyncio.open_connection(sock=sock)

        client = TelegramClient(
            StringSession(SESSION_STRING), API_ID, API_HASH,
            connection=None,
            auto_reconnect=True
        )
        print(f"🔗 Using proxy: {proxy_str}")
    else:
        print("⚠️ No proxy, connecting directly...")
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

    try:
        print("Telethon connecting...")
        telethon_loop.run_until_complete(client.connect())
        if not telethon_loop.run_until_complete(client.is_user_authorized()):
            print("❌ ERROR: Session invalid!")
            return
        me = telethon_loop.run_until_complete(client.get_me())
        print(f"✅ Connected as: {me.first_name} (@{me.username})")
        telethon_loop.run_forever()
    except Exception as e:
        print(f"❌ Telethon Start Error: {e}")


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    t = threading.Thread(target=start_telethon, daemon=True)
    t.start()
    time.sleep(10)
    print(f"🚀 Flask running on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
