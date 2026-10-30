import os
import sqlite3
import time
import hashlib
import requests
from flask import Flask, request, render_template_string
from dotenv import load_dotenv
from ed25519_blake2b import SigningKey

load_dotenv()

app = Flask(__name__)

SOURCE         = (os.getenv("SOURCE") or "").strip()
SEED           = (os.getenv("SEED") or os.getenv("PRIVATE_KEY") or "").strip()
SEED_INDEX     = int(os.getenv("SEED_INDEX", os.getenv("INDEX", "0")))
RPC_KEY        = os.getenv("RPC_KEY", "")
REWARD         = int(os.getenv("REWARD", "3000000000000000000000000"))
NODE           = os.getenv("NODE", "https://rpc.nano.to")
DB             = os.getenv("DB", "faucet.db")
COOLDOWN       = int(os.getenv("COOLDOWN", "86400"))
TURNSTILE_SITE = os.getenv("TURNSTILE_SITE_KEY", "")
TURNSTILE_SEC  = os.getenv("TURNSTILE_SECRET_KEY", "")

A = "13456789abcdefghijkmnopqrstuwxyz"

def decode_address(addr):
    p = addr[5:57]
    bits = ''.join(bin(A.index(c))[2:].zfill(5) for c in p)[4:]
    return bytes.fromhex(''.join(hex(int(bits[i:i+8],2))[2:].zfill(2) for i in range(0,len(bits),8)))

def valid_address(addr):
    if not addr or len(addr) != 65 or not addr.startswith("nano_"):
        return False
    try:
        p, cs = addr[5:57], addr[57:65]
        bits = ''.join(bin(A.index(c))[2:].zfill(5) for c in p)[4:]
        pub = bytes.fromhex(''.join(hex(int(bits[i:i+8],2))[2:].zfill(2) for i in range(0,len(bits),8)))
        ck = hashlib.blake2b(pub, digest_size=5).digest()[::-1]
        cb = ''.join(bin(b)[2:].zfill(8) for b in ck)
        return cs == ''.join(A[int(cb[i:i+5],2)] for i in range(0,40,5))
    except Exception:
        return False

if not SEED:
    raise SystemExit("SEED (или PRIVATE_KEY) не задан в .env")
if not SOURCE:
    raise SystemExit("SOURCE не задан в .env")
try:
    PRIVATE_KEY = hashlib.blake2b(bytes.fromhex(SEED) + SEED_INDEX.to_bytes(4,'big'), digest_size=32).digest()
except Exception as e:
    raise SystemExit(f"плохой SEED: {e}")

try:
    sk = SigningKey(PRIVATE_KEY)
    vk = sk.get_verifying_key() if hasattr(sk, "get_verifying_key") else sk.get_verify_key()
    derived_pub = vk.to_bytes() if hasattr(vk, "to_bytes") else bytes(vk)
    if derived_pub != decode_address(SOURCE):
        raise SystemExit(f"SEED[#{SEED_INDEX}] не соответствует SOURCE={SOURCE}")
except SystemExit:
    raise
except Exception as e:
    print(f"WARNING: не удалось проверить seed/source: {e}")

def init_db():
    c = sqlite3.connect(DB)
    c.execute("create table if not exists claims (ip text, wallet text, ts integer)")
    c.execute("create index if not exists idx_claims_ts on claims(ts)")
    c.commit()
    c.close()
init_db()

HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>nano faucet</title>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#ffffff;color:#000000;font-family:-apple-system,BlinkMacSystemFont,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}
.main{display:flex;flex-direction:column;align-items:center;gap:10px;width:100%;max-width:340px;padding:0 16px}
.title{color:#444444;font-size:24px;font-weight:800;letter-spacing:-0.5px;margin-bottom:10px}
.logo{width:120px;height:120px;border-radius:50%;object-fit:cover;margin-bottom:4px}
form{display:flex;gap:8px;width:100%}
input{flex:1;padding:12px;border:1px solid #cccccc;border-radius:8px;font-size:14px;font-family:monospace;outline:none;min-width:0}
input:focus{border-color:#0052ff}
button{padding:12px 16px;background:#0052ff;color:#ffffff;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer}
button:hover{background:#0043d1}
button:disabled{background:#a0c0ff;cursor:not-allowed}
.msg{font-size:13px;color:#ff0000;min-height:16px;text-align:center;margin-top:2px}
</style>
</head>
<body>
<div class="main">
<div class="title">nano faucet</div>
<img src="https://s2.coinmarketcap.com/static/img/coins/64x64/1567.png" class="logo" alt="xno">
<form method="post" action="/claim" onsubmit="this.querySelector('button').disabled=true">
<input type="text" name="wallet" placeholder="nano_..." autocomplete="off" spellcheck="false" required>
<button type="submit">enter</button>
</form>
<div class="msg">{{ msg }}</div>
{% if sitekey %}<div class="cf-turnstile" data-sitekey="{{ sitekey }}"></div>{% endif %}
</div>
</body>
</html>
"""

def page(msg=""):
    return render_template_string(HTML, msg=msg, sitekey=TURNSTILE_SITE)

def client_ip():
    for h in ("CF-Connecting-IP", "X-Real-IP"):
        v = request.headers.get(h)
        if v:
            return v.strip()
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"

def verify_captcha(token, ip):
    if not TURNSTILE_SEC:
        return True
    if not token:
        return False
    try:
        r = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": TURNSTILE_SEC, "response": token, "remoteip": ip},
            timeout=5,
        ).json()
        return bool(r.get("success"))
    except Exception:
        return False

def send_nano(dest, amount):
    headers = {"Authorization": RPC_KEY, "Content-Type": "application/json"}

    try:
        info = requests.post(NODE, json={"action":"account_info","account":SOURCE},
                             headers=headers, timeout=10).json()
    except Exception as e:
        return {"error": f"node: {e}"}
    if "frontier" not in info:
        return {"error": f"account_info: {info.get('error', info)}"}

    frontier = info["frontier"]
    balance  = int(info["balance"])
    if balance < amount:
        return {"error": "faucet empty"}
    new_balance = balance - amount
    rep = info.get("representative", SOURCE)

    try:
        w = requests.post(NODE, json={"action":"work_generate","hash":frontier},
                          headers=headers, timeout=30).json()
    except Exception as e:
        return {"error": f"work: {e}"}
    if "work" not in w:
        return {"error": f"work: {w.get('error', w)}"}

    pub       = decode_address(SOURCE)
    rep_pub   = decode_address(rep)
    dest_pub  = decode_address(dest)

    data = b'\x00'*31 + b'\x06' + pub + bytes.fromhex(frontier) + rep_pub \
           + new_balance.to_bytes(16, 'big') + dest_pub
    block_hash = hashlib.blake2b(data, digest_size=32).digest()
    sig = SigningKey(PRIVATE_KEY).sign(block_hash).hex().upper()

    block = {
        "type": "state",
        "account": SOURCE,
        "previous": frontier,
        "representative": rep,
        "balance": str(new_balance),
        "link": dest_pub.hex(),
        "signature": sig,
        "work": w["work"],
    }
    try:
        p = requests.post(NODE, json={"action":"process","json_block":"true",
                                      "subtype":"send","block":block},
                          headers=headers, timeout=10).json()
    except Exception as e:
        return {"error": f"process: {e}"}
    return p

@app.route("/")
def index():
    return page("")

@app.route("/claim", methods=["POST"])
def claim():
    wallet = (request.form.get("wallet") or "").strip()
    ip     = client_ip()
    now    = int(time.time())
    day_ago = now - COOLDOWN

    if not valid_address(wallet):
        return page("invalid address")
    if wallet == SOURCE:
        return page("cannot claim to faucet itself")

    token = request.form.get("cf-turnstile-response", "")
    if not verify_captcha(token, ip):
        return page("captcha failed")

    c = sqlite3.connect(DB)
    try:
        row = c.execute(
            "select 1 from claims where (ip=? or wallet=?) and ts>?",
            (ip, wallet, day_ago),
        ).fetchone()
        if row:
            return page("wait 24h")

        result = send_nano(wallet, REWARD)
        if "hash" in result:
            c.execute("insert into claims values (?,?,?)", (ip, wallet, now))
            c.commit()
            return page(f"sent {REWARD/10**30:.6f} XNO")
        print(f"send error: {result}")
        return page("send failed, try later")
    finally:
        c.close()

if __name__ == "__main__":
    if not TURNSTILE_SITE:
        print("WARNING: капча не настроена (TURNSTILE_SITE_KEY пуст)")
    print(f"faucet  : {SOURCE}")
    print(f"reward  : {REWARD/10**30:.6f} XNO")
    print(f"index   : {SEED_INDEX}")
    app.run(host="0.0.0.0", port=5000)
