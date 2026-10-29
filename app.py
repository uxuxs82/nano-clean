import os
import sqlite3
import time
import requests
from flask import Flask, request, render_template_string
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
SOURCE = os.getenv("SOURCE")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_KEY = os.getenv("RPC_KEY")
REWARD = os.getenv("REWARD", "3000000000000000000000000")
NODE = "https://rpc.nano.to"
DB = "faucet.db"

def init():
    c = sqlite3.connect(DB)
    c.execute("create table if not exists claims (ip text, wallet text, ts integer)")
    c.commit()
    c.close()

init()

HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>nano faucet</title>
<script src="https://cdn.jsdelivr.net/gh/xsukax/xsukax-JS-CAPTCHA/captcha.js"></script>
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
<div class="cf-turnstile"></div>
</div>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML, msg="")

@app.route("/claim", methods=["POST"])
def claim():
    wallet = request.form.get("wallet", "").strip()
    ip = request.remote_addr
    now = int(time.time())
    day_ago = now - 86400

    if not wallet.startswith("nano_"):
        return render_template_string(HTML, msg="invalid address")

    c = sqlite3.connect(DB)
    row = c.execute(
        "select 1 from claims where (ip=? or wallet=?) and ts>?",
        (ip, wallet, day_ago)
    ).fetchone()

    if row:
        c.close()
        return render_template_string(HTML, msg="wait 24h")

    payload = {
        "action": "send",
        "source": SOURCE,
        "destination": wallet,
        "amount": REWARD,
        "key": PRIVATE_KEY
    }
    headers = {"Authorization": RPC_KEY, "Content-Type": "application/json"}

    try:
        r = requests.post(NODE, json=payload, headers=headers, timeout=10).json()
    except Exception:
        c.close()
        return render_template_string(HTML, msg="node offline")

    if "block" in r:
        c.execute("insert into claims values (?,?,?)", (ip, wallet, now))
        c.commit()
        c.close()
        return render_template_string(HTML, msg="sent")

    c.close()
    return render_template_string(HTML, msg="send failed")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
