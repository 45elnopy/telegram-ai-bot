import requests
import time
import pandas as pd
from binance.client import Client
import json
import os
import threading

# ================= CONFIG =================
BOT_TOKEN = "8404928684:AAHIO2ZXYBkr5IttEXZnh_Yooaq4QLx24pk"
CHAT_ID = "1019525343"
INTERVAL = Client.KLINE_INTERVAL_15MINUTE
LIMIT = 200

SYMBOLS_FILE = "symbols.txt"
SIGNALS_FILE = "last_signals.json"
TRADES_FILE = "open_trades.json"
STATS_FILE = "stats.json"
USERS_FILE = "users.json"

LAST_UPDATE = 0
UPDATE_INTERVAL = 60 * 60 * 24  # تحديث يومي للأزواج
OFFSET = 457556566  # أحدث offset
# =========================================

# ------------------ Telegram ------------------
def send_telegram(chat_id, message, reply_markup=None):
    data = {"chat_id": chat_id, "text": message}
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=data)

def send_telegram_all(message, filter_type=None):
    users = load_json(USERS_FILE, {})
    for chat_id, u in users.items():
        if not filter_type or u.get("type","All Pairs")==filter_type or u.get("type","All Pairs")=="All Pairs":
            send_telegram(chat_id, message)

# ------------------ JSON utils ------------------
def load_json(file, default):
    if os.path.exists(file):
        with open(file, "r") as f:
            return json.load(f)
    return default

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f)

# ------------------ رسالة ترحيب مع أزرار ------------------
def send_welcome_buttons(chat_id):
    markup = {
        "inline_keyboard": [
            [{"text":"BTC Only","callback_data":"BTC Only"}],
            [{"text":"Altcoins Only","callback_data":"Altcoins Only"}],
            [{"text":"All Pairs","callback_data":"All Pairs"}],
            [{"text":"Signals VIP","callback_data":"Signals VIP"}]
        ]
    }
    send_telegram(chat_id, "👋 أهلاً! اختر نوع الإشارات التي تريد تلقيها:", reply_markup=markup)

# ------------------ تحديث الأزواج ------------------
def update_symbols_from_binance(file_path=SYMBOLS_FILE):
    try:
        data = requests.get("https://api.binance.com/api/v3/ticker/24hr").json()
        symbols = [
            i["symbol"] for i in data
            if i["symbol"].endswith("USDT")
            and not i["symbol"].endswith(("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT"))
        ]
        with open(file_path, "w") as f:
            for s in symbols:
                f.write(s + "\n")
        send_telegram_all(f"✅ تم تحديث الأزواج تلقائياً ({len(symbols)} زوج)")
    except Exception as e:
        send_telegram_all(f"⚠️ خطأ تحديث الأزواج: {e}")

def load_symbols():
    try:
        with open(SYMBOLS_FILE) as f:
            return [l.strip() for l in f if l.strip()]
    except:
        return []

# ------------------ Binance ------------------
client = Client()

def get_data(symbol):
    klines = client.get_klines(symbol=symbol, interval=INTERVAL, limit=LIMIT)
    df = pd.DataFrame(klines, columns=[
        'time','open','high','low','close','volume',
        'ct','qav','trades','tb','tq','ig'
    ])
    df['close'] = df['close'].astype(float)
    return df

# ------------------ Indicators ------------------
def RSI(series, p=14):
    d = series.diff()
    g = d.where(d > 0, 0).rolling(p).mean()
    l = -d.where(d < 0, 0).rolling(p).mean()
    rs = g / l
    return 100 - (100 / (1 + rs))

def EMA(series, p):
    return series.ewm(span=p, adjust=False).mean()

def MACD(series):
    return EMA(series,12) - EMA(series,26), EMA(EMA(series,12)-EMA(series,26),9)

# ------------------ Analysis ------------------
def analyze(df):
    df['rsi'] = RSI(df['close'])
    df['ema50'] = EMA(df['close'], 50)
    df['ema200'] = EMA(df['close'], 200)
    df['macd'], df['macd_signal'] = MACD(df['close'])
    last = df.iloc[-1]

    if last['rsi'] < 30 and last['ema50'] > last['ema200'] and last['macd'] > last['macd_signal']:
        return "BUY", last['close']
    if last['rsi'] > 70 and last['ema50'] < last['ema200'] and last['macd'] < last['macd_signal']:
        return "SELL", last['close']
    return None, None

# ------------------ إدارة الصفقات ------------------
def check_trades():
    trades = load_json(TRADES_FILE, {})
    stats = load_json(STATS_FILE, {"win":0,"loss":0})

    for symbol in list(trades.keys()):
        price = get_data(symbol).iloc[-1]['close']
        t = trades[symbol]

        if t["type"] == "BUY":
            if price >= t["tp"]:
                stats["win"] += 1
                send_telegram_all(f"✅ WIN {symbol}", t.get("filter"))
                trades.pop(symbol)
            elif price <= t["sl"]:
                stats["loss"] += 1
                send_telegram_all(f"❌ LOSS {symbol}", t.get("filter"))
                trades.pop(symbol)

        if t["type"] == "SELL":
            if price <= t["tp"]:
                stats["win"] += 1
                send_telegram_all(f"✅ WIN {symbol}", t.get("filter"))
                trades.pop(symbol)
            elif price >= t["sl"]:
                stats["loss"] += 1
                send_telegram_all(f"❌ LOSS {symbol}", t.get("filter"))
                trades.pop(symbol)

    save_json(TRADES_FILE, trades)
    save_json(STATS_FILE, stats)

# ------------------ مراقبة رسائل المستخدمين ------------------
def check_telegram_messages():
    global OFFSET
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={OFFSET}&timeout=1"
    try:
        resp = requests.get(url, timeout=10).json()
        updates = resp.get("result", [])
        users = load_json(USERS_FILE, {})

        for update in updates:
            OFFSET = max(OFFSET, update["update_id"] + 1)
            if "message" in update:
                chat_id = str(update["message"]["chat"]["id"])
                text = update["message"].get("text", "")
                print(f"Received message from {chat_id}: {text}")
                if text == "/start":
                    if chat_id not in users:
                        users[chat_id] = {"joined_at": time.time(), "type":"All Pairs"}
                        save_json(USERS_FILE, users)
                    send_welcome_buttons(chat_id)

            if "callback_query" in update:
                chat_id = str(update["callback_query"]["message"]["chat"]["id"])
                data = update["callback_query"]["data"]
                users[chat_id]["type"] = data
                save_json(USERS_FILE, users)
                send_telegram(chat_id, f"✅ تم اختيار {data} بنجاح!")
        return True
    except Exception as e:
        print(f"Error in check_telegram_messages: {e}")
        return False

# ------------------ Main Bot ------------------
def run_bot():
    global LAST_UPDATE

    if time.time() - LAST_UPDATE > UPDATE_INTERVAL:
        update_symbols_from_binance()
        LAST_UPDATE = time.time()

    symbols = load_symbols()
    last_signals = load_json(SIGNALS_FILE, {})
    trades = load_json(TRADES_FILE, {})
    users = load_json(USERS_FILE, {})

    for symbol in symbols:
        signal, price = analyze(get_data(symbol))
        if not signal:
            continue

        # إرسال إشعار فوري عند تغير الإشارة
        if last_signals.get(symbol) and last_signals[symbol] != signal:
            send_telegram_all(f"⚠️ تغيرت إشارة {symbol} من {last_signals[symbol]} إلى {signal}")

        if symbol in trades:
            continue
        if last_signals.get(symbol) == signal:
            continue

        tp = price * (1.02 if signal == "BUY" else 0.98)
        sl = price * (0.99 if signal == "BUY" else 1.01)

        for chat_id, u in users.items():
            filter_type = u.get("type","All Pairs")
            if filter_type=="All Pairs" or \
               (filter_type=="BTC Only" and symbol=="BTCUSDT") or \
               (filter_type=="Altcoins Only" and symbol!="BTCUSDT") or \
               (filter_type=="Signals VIP"):
                send_telegram(chat_id, f"""
🚀 AI CRYPTO SIGNAL 🚀
Pair: {symbol}
Signal: {signal}
Entry: {round(price,4)}
TP: {round(tp,4)}
SL: {round(sl,4)}
TF: 15M
""")

        last_signals[symbol] = signal
        trades[symbol] = {"type":signal,"entry":price,"tp":tp,"sl":sl,"filter":filter_type}
        save_json(SIGNALS_FILE, last_signals)
        save_json(TRADES_FILE, trades)

# ------------------ Threads ------------------
def telegram_thread():
    while True:
        check_telegram_messages()
        time.sleep(0.5)  # فحص أسرع للزر

def bot_thread():
    while True:
        try:
            run_bot()
            check_trades()
        except Exception as e:
            send_telegram_all(f"⚠️ Error: {e}")
        time.sleep(3)

# ------------------ Scheduler ------------------
print("🤖 Bot is running...")
threading.Thread(target=telegram_thread, daemon=True).start()
threading.Thread(target=bot_thread, daemon=True).start()

while True:
    time.sleep(1)
