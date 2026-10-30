import os
import sqlite3
import time
import hashlib
import random
import base64
import io
import requests
from flask import Flask, request, render_template_string, session
from dotenv import load_dotenv
from py_ed25519_blake2b import SigningKey
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

REWARD     = 3000000000000000000000000
NODE       = "https://rpc.nano.to"
SEED_INDEX = 0

SOURCE     = (os.getenv("SOURCE") or "").strip()
SEED       = (os.getenv("SEED") or os.getenv("PRIVATE_KEY") or "").strip()
RPC_KEY    = os.getenv("RPC_KEY", "")
SECRET_KEY = os.getenv("SECRET_KEY")
DB         = os.getenv("DB", "faucet.db")
COOLDOWN   = int(os.getenv("COOLDOWN", "86400"))

app = Flask(__name__)
app.secret_key = SECRET_KEY or os.urandom(24).hex()

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
    raise SystemExit("SEED not set")
if not SOURCE:
    raise SystemExit("SOURCE not set")

PRIVATE_KEY = hashlib.blake2b(bytes.fromhex(SEED) + SEED_INDEX.to_bytes(4,'big'), digest_size=32).digest()

sk = SigningKey(PRIVATE_KEY)
vk = sk.get_verifying_key() if hasattr(sk, "get_verifying_key") else sk.get_verify_key()
derived_pub = vk.to_bytes() if hasattr(vk, "to_bytes") else bytes(vk)
if derived_pub != decode_address(SOURCE):
    raise SystemExit(f"SEED[#{SEED_INDEX}] mismatch with SOURCE={SOURCE}")

def init_db():
    c = sqlite3.connect(DB)
    c.execute("create table if not exists claims (ip text, wallet text, ts integer)")
    c.execute("create index if not exists idx_claims_ts on claims(ts)")
    c.commit()
    c.close()
init_db()

def generate_captcha_image(text):
    w, h = 170, 55
    img = Image.new('RGB', (w, h), color='#f5f5f5')
    draw = ImageDraw.Draw(img)
    for _ in range(7):
        draw.line([(random.randint(0,w), random.randint(0,h)),
                   (random.randint(0,w), random.randint(0,h))],
                  fill=(random.randint(140,200),)*3, width=1)
    for _ in range(180):
        draw.point((random.randint(0,w), random.randint(0,h)),
                   fill=(random.randint(180,220),)*3)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
    except (OSError, IOError):
        try:
            font = ImageFont.load_default(size=30)
        except TypeError:
            font = ImageFont.load_default()
    for i, ch in enumerate(text):
        x = 12 + i * 30 + random.randint(-4, 4)
        y = 8 + random.randint(-6, 6)
        draw.text((x, y), ch, fill=(random.randint(0,70),)*3, font=font)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()

HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>nano faucet</title>
<script src="https://cdn.jsdelivr.net/npm/@fingerprintjs/botd@1/dist/botd.min.js"></script>
<script src="https://unpkg.com/bot-signal"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#ffffff;color:#000000;font-family:-apple-system,BlinkMacSystemFont,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}
.main{display:flex;flex-direction:column;align-items:center;gap:10px;width:100%;max-width:380px;padding:0 16px}
.title{color:#444444;font-size:24px;font-weight:800;letter-spacing:-0.5px;margin-bottom:10px}
.logo{width:120px;height:120px;border-radius:50%;object-fit:cover;margin-bottom:4px}
form{display:flex;flex-direction:column;gap:8px;width:100%}
input[type=text]{padding:12px;border:1px solid #cccccc;border-radius:8px;font-size:14px;font-family:monospace;outline:none;width:100%}
input[type=text]:focus{border-color:#00bfff}
button{padding:12px 16px;background:#00bfff;color:#ffffff;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer}
button:hover{background:#00a3dd}
button:disabled{background:#b8e5f7;cursor:not-allowed}
.msg{font-size:13px;color:#dc2626;min-height:16px;text-align:center;margin-top:2px}
.msg.ok{color:#16a34a}
.captcha-row{display:flex;gap:8px;align-items:center;width:100%}
.captcha-row img{border-radius:6px;border:1px solid #ddd;height:55px}
.captcha-row input{flex:1}
</style>
</head>
<body>
<div class="main">
<div class="title">nano faucet</div>
<img src="https://s2.coinmarketcap.com/static/img/coins/64x64/1567.png" class="logo" alt="xno">
<form id="faucet-form" method="post" action="/claim">
<input type="text" name="wallet" placeholder="nano_" autocomplete="off" spellcheck="false" required>
<div class="captcha-row">
  <img src="data:image/png;base64,{{ captcha_img }}" alt="captcha">
  <input type="text" name="captcha" placeholder="captcha" autocomplete="off" required maxlength="6">
</div>
<input type="text" name="website" id="website" style="display:none" tabindex="-1" autocomplete="off">
<input type="hidden" name="form_time" id="form_time" value="">
<input type="hidden" name="bot_flag" id="bot_flag" value="false">
<input type="hidden" name="botsignal_flag" id="botsignal_flag" value="unknown">
<button type="submit" id="submit-btn">enter</button>
</form>
<div class="msg{% if 'sent' in msg %} ok{% endif %}">{{ msg }}</div>
</div>
<script>
document.getElementById('form_time').value = Date.now();
(function(){
  if (typeof Botd !== 'undefined') {
    Botd.load().then(function(d){ return d.detect(); })
      .then(function(r){ document.getElementById('bot_flag').value = r.bot ? 'true' : 'false'; })
      .catch(function(){});
  }
  if (typeof BotSignal !== 'undefined' && typeof BotSignal.isHuman === 'function') {
    try {
      var h = BotSignal.isHuman(window);
      document.getElementById('botsignal_flag').value = h ? 'human' : 'bot';
    } catch (e) {}
  }
})();
</script>
</body>
</html>
"""

def page(msg=""):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    captcha_text = ''.join(random.choices(alphabet, k=5))
    session["captcha_text"] = captcha_text
    img_b64 = generate_captcha_image(captcha_text)
    return render_template_string(HTML, msg=msg, captcha_img=img_b64)

def client_ip():
    for h in ("CF-Connecting-IP", "X-Real-IP"):
        v = request.headers.get(h)
        if v: return v.strip()
    xff = request.headers.get("X-Forwarded-For")
    if xff: return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"

def send_nano(dest, amount):
    headers = {"Authorization": RPC_KEY, "Content-Type": "application/json"}
    try:
        info = requests.post(NODE, json={"action":"account_info","account":SOURCE},
                             headers=headers, timeout=10).json()
    except Exception as e:
        return {"error": f"node: {e}"}
    if "frontier" not in info:
        return {"error": f"account_info: {info.get('error', info)}"}
    frontier = info["frontier"]; balance = int(info["balance"])
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
    pub = decode_address(SOURCE)
    rep_pub = decode_address(rep)
    dest_pub = decode_address(dest)
    data = b'\x00'*31 + b'\x06' + pub + bytes.fromhex(frontier) + rep_pub \
           + new_balance.to_bytes(16, 'big') + dest_pub
    block_hash = hashlib.blake2b(data, digest_size=32).digest()
    sig = SigningKey(PRIVATE_KEY).sign(block_hash).hex().upper()
    block = {"type":"state","account":SOURCE,"previous":frontier,"representative":rep,
             "balance":str(new_balance),"link":dest_pub.hex(),"signature":sig,"work":w["work"]}
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
    ip = client_ip()
    now = int(time.time())
    day_ago = now - COOLDOWN

    if request.form.get("website"):
        return page("bot detected")

    if request.form.get("botsignal_flag", "unknown") == "bot":
        return page("bot detected")

    if request.form.get("bot_flag", "false") == "true":
        return page("bot detected")

    form_time = request.form.get("form_time", "")
    if form_time and form_time.isdigit():
        if int(time.time() * 1000) - int(form_time) < 1500:
            return page("bot detected")

    answer = (request.form.get("captcha") or "").strip().upper()
    correct = session.pop("captcha_text", None)
    if not correct or answer != correct:
        return page("wrong captcha")

    if not valid_address(wallet):
        return page("invalid address")
    if wallet == SOURCE:
        return page("cannot claim to faucet itself")

    c = sqlite3.connect(DB)
    try:
        row = c.execute("select 1 from claims where (ip=? or wallet=?) and ts>?",
                        (ip, wallet, day_ago)).fetchone()
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
    print(f"faucet  : {SOURCE}")
    print(f"reward  : {REWARD/10**30:.6f} XNO")
    print(f"index   : {SEED_INDEX}")
    app.run(host="0.0.0.0", port=5000)
