import os
import json
import sqlite3
import threading
import time
import base64
import secrets
from flask import Flask, request, jsonify, Response
import websocket
from pywebpush import webpush, WebPushException
from py_vapid import Vapid
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

app = Flask(__name__)

DB = os.path.join(os.path.dirname(__file__), "xrp_alert.db")
lock = threading.Lock()
current_price = None

HTML = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>XRP Alert Binance</title>
<style>
body{margin:0;background:#0b1020;color:#fff;font-family:Arial,sans-serif}
.wrap{max-width:520px;margin:auto;padding:22px}
.card{background:#151d33;border-radius:20px;padding:22px;margin-top:18px}
h1{margin:0 0 8px;font-size:28px}.muted{color:#9da8c0}
.price{font-size:42px;font-weight:800;margin:22px 0}
.badge{display:inline-block;padding:7px 12px;border-radius:20px;background:#26304b}
.live{background:#123f2a;color:#55e39b}
input,button{width:100%;box-sizing:border-box;padding:15px;border-radius:12px;font-size:16px}
input{margin:10px 0;background:#0d1426;border:1px solid #35415f;color:#fff}
button{border:0;margin-top:10px;background:#2f7df6;color:#fff;font-weight:700}
button.secondary{background:#29334d}.alert{display:flex;justify-content:space-between;gap:10px;align-items:center;border-top:1px solid #29334d;padding:13px 0}
.small{font-size:13px;color:#9da8c0}.danger{background:#7b2630;width:auto;padding:9px 12px}
</style>
</head>
<body>
<div class="wrap">
  <h1>🔔 XRP Alert</h1>
  <div class="muted">Binance Futures • XRP/USDT Perpetual</div>
  <div class="card">
    <span id="status" class="badge">Connexion…</span>
    <div id="price" class="price">--.----</div>
    <div class="small">Prix en temps réel</div>
  </div>
  <div class="card">
    <h2>Créer une alerte</h2>
    <input id="target" type="number" step="0.0001" placeholder="Ex : 3.0000">
    <button onclick="addAlert()">Activer l’alerte</button>
    <button class="secondary" onclick="enablePush()">🔔 Activer les notifications</button>
    <button class="secondary" onclick="testPush()">Tester la notification</button>
    <div id="msg" class="small" style="margin-top:12px"></div>
  </div>
  <div class="card">
    <h2>Mes alertes</h2>
    <div id="alerts">Chargement…</div>
  </div>
</div>
<script>
let vapidKey="";
const $=id=>document.getElementById(id);
function msg(t){$("msg").textContent=t}
async function init(){
  if(!("serviceWorker" in navigator) || !("PushManager" in window)){msg("Les notifications Push ne sont pas prises en charge ici.");return}
  await navigator.serviceWorker.register("/sw.js");
  const r=await fetch("/api/vapid-public-key"); vapidKey=(await r.json()).key;
  refreshAlerts();
  setInterval(status,1000); status();
}
function b64ToUint8(s){s=s.replace(/-/g,"+").replace(/_/g,"/");while(s.length%4)s+="=";return Uint8Array.from(atob(s),c=>c.charCodeAt(0))}
async function enablePush(){
  const p=await Notification.requestPermission();
  if(p!=="granted"){msg("Autorisation des notifications refusée.");return}
  const reg=await navigator.serviceWorker.ready;
  let sub=await reg.pushManager.getSubscription();
  if(!sub) sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:b64ToUint8(vapidKey)});
  await fetch("/api/subscribe",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(sub)});
  msg("✅ Notifications activées. Tu peux fermer la page.");
}
async function testPush(){await fetch("/api/test-notification",{method:"POST"});msg("Notification de test envoyée.")}
async function addAlert(){
  const target=parseFloat($("target").value); if(!target){msg("Entre un prix.");return}
  await fetch("/api/alerts",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({target})});
  $("target").value=""; refreshAlerts(); msg("Alerte activée.");
}
async function del(id){await fetch("/api/alerts/"+id,{method:"DELETE"});refreshAlerts()}
async function refreshAlerts(){
  const a=await (await fetch("/api/alerts")).json();
  $("alerts").innerHTML=a.length?a.map(x=>`<div class="alert"><span>🎯 ${Number(x.target).toFixed(4)} USDT<br><span class="small">${x.triggered?"Déclenchée":"Active"}</span></span><button class="danger" onclick="del(${x.id})">Supprimer</button></div>`).join(""):"<div class='small'>Aucune alerte.</div>";
}
async function status(){
  try{const s=await (await fetch("/api/status")).json();$("status").textContent=s.online?"● En Direct":"Serveur";$("status").className="badge "+(s.online?"live":"");$("price").textContent=s.price?s.price.toFixed(4):"--.----"}catch(e){$("status").textContent="Hors ligne"}}
init();
</script>
</body>
</html>"""

def db():
    c = sqlite3.connect(DB, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS alerts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target REAL NOT NULL,
        triggered INTEGER DEFAULT 0,
        created_at REAL NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS subscriptions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        endpoint TEXT UNIQUE NOT NULL,
        data TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS config(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    c.commit()
    return c

conn = db()

def get_config(k):
    row = conn.execute("SELECT value FROM config WHERE key=?", (k,)).fetchone()
    return row["value"] if row else None

def set_config(k,v):
    conn.execute("INSERT OR REPLACE INTO config(key,value) VALUES(?,?)",(k,v))
    conn.commit()

def ensure_vapid():
    pub = get_config("vapid_public")
    priv = get_config("vapid_private")
    if pub and priv:
        return pub, priv
    v = Vapid()
    v.generate_keys()
    pub_bytes = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
    # pywebpush accepts a PEM private key string.
    priv_pem = v.private_key.private_bytes(
        Encoding.PEM,
        format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["PrivateFormat"]).PrivateFormat.PKCS8,
        encryption_algorithm=__import__("cryptography.hazmat.primitives.serialization", fromlist=["NoEncryption"]).NoEncryption()
    ).decode()
    set_config("vapid_public", pub_b64)
    set_config("vapid_private", priv_pem)
    return pub_b64, priv_pem

def send_push(title, body):
    pub, priv = ensure_vapid()
    rows = conn.execute("SELECT id,endpoint,data FROM subscriptions").fetchall()
    dead=[]
    for r in rows:
        try:
            sub=json.loads(r["data"])
            webpush(
                subscription_info=sub,
                data=json.dumps({"title":title,"body":body}),
                vapid_private_key=priv,
                vapid_claims={"sub":os.getenv("VAPID_CLAIMS_EMAIL","mailto:alert@example.com")}
            )
        except Exception as e:
            # Remove subscriptions that are no longer valid.
            text=str(e)
            if "404" in text or "410" in text:
                dead.append(r["id"])
    for i in dead:
        conn.execute("DELETE FROM subscriptions WHERE id=?",(i,))
    conn.commit()

def check_alerts(price):
    rows = conn.execute("SELECT id,target FROM alerts WHERE triggered=0").fetchall()
    for r in rows:
        if price >= r["target"]:
            conn.execute("UPDATE alerts SET triggered=1 WHERE id=?",(r["id"],))
            conn.commit()
            send_push("🚨 XRP Alert", f"XRP/USDT a atteint {price:.4f} USDT (cible {r['target']:.4f}).")

def binance_worker():
    global current_price
    while True:
        try:
            def on_message(ws, message):
                global current_price
                try:
                    data=json.loads(message)
                    p=float(data["p"])
                    current_price=p
                    check_alerts(p)
                except Exception:
                    pass
            ws=websocket.WebSocketApp(
                "wss://fstream.binance.com/ws/xrpusdt@trade",
                on_message=on_message
            )
            ws.run_forever(ping_interval=20,ping_timeout=10)
        except Exception:
            pass
        time.sleep(3)

threading.Thread(target=binance_worker,daemon=True).start()

@app.get("/")
def home(): return Response(HTML,mimetype="text/html")

@app.get("/sw.js")
def sw():
    js="""self.addEventListener('push',e=>{let d={};try{d=e.data.json()}catch(x){d={title:'XRP Alert',body:e.data?e.data.text():'Alerte'}}e.waitUntil(self.registration.showNotification(d.title||'XRP Alert',{body:d.body||'',icon:'/icon.svg',badge:'/icon.svg',requireInteraction:true}))});self.addEventListener('notificationclick',e=>{e.notification.close();e.waitUntil(clients.openWindow('/'))});"""
    return Response(js,mimetype="application/javascript")

@app.get("/icon.svg")
def icon():
    return Response("""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192"><rect width="192" height="192" rx="40" fill="#151d33"/><text x="96" y="120" text-anchor="middle" font-size="100">🔔</text></svg>""",mimetype="image/svg+xml")

@app.get("/api/status")
def status(): return jsonify({"online":current_price is not None,"price":current_price})

@app.get("/api/vapid-public-key")
def vapid_key(): return jsonify({"key":ensure_vapid()[0]})

@app.post("/api/subscribe")
def subscribe():
    data=request.get_json(force=True)
    endpoint=data.get("endpoint")
    if not endpoint: return jsonify({"ok":False}),400
    conn.execute("INSERT OR REPLACE INTO subscriptions(endpoint,data) VALUES(?,?)",(endpoint,json.dumps(data)))
    conn.commit()
    return jsonify({"ok":True})

@app.post("/api/test-notification")
def test():
    send_push("🔔 XRP Alert", "Notification de test reçue.")
    return jsonify({"ok":True})

@app.get("/api/alerts")
def list_alerts():
    rows=conn.execute("SELECT id,target,triggered FROM alerts ORDER BY id DESC").fetchall()
    return jsonify([dict(r) for r in rows])

@app.post("/api/alerts")
def add():
    data=request.get_json(force=True)
    target=float(data["target"])
    conn.execute("INSERT INTO alerts(target,created_at) VALUES(?,?)",(target,time.time()))
    conn.commit()
    return jsonify({"ok":True})

@app.delete("/api/alerts/<int:aid>")
def delete(aid):
    conn.execute("DELETE FROM alerts WHERE id=?",(aid,))
    conn.commit()
    return jsonify({"ok":True})

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","8000")))
