import os
import json
import hmac
import hashlib
import logging
import random
from datetime import datetime
from threading import Thread
from urllib.parse import parse_qsl

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import psycopg2
import pandas as pd
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TradingBot")

TOKEN = os.environ.get("BOT_TOKEN", "").strip() or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").strip()  # sans le @, pour les liens dans les alertes
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()  # canal où publier les alertes marché (optionnel)
ADMIN_ID_RAW = os.environ.get("ADMIN_ID", "0").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown") if TOKEN else None
URL_MINI_APP = os.environ.get("URL_MINI_APP_TRADING", "https://trading-3wcr.onrender.com")

# Offres partenaires : un simple encart marketing, jamais une barrière d'accès.
EXNESS_LINK = "https://one.exnessonelink.com/a/395vyusacl"
EXNESS_PROMO = "395vyusacl"
KUCOIN_LINK = "https://www.kucoin.com/ucenter/signup?&rcode=rEN8V1E&utm_medium=U17710"
KUCOIN_PROMO = "rEN8V1E"

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.png")


# --- BASE DE DONNÉES POSTGRESQL (PERSISTANTE) ---
def get_db():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    if not DATABASE_URL:
        logger.error("❌ DATABASE_URL est manquant — la base de données ne sera pas disponible.")
        return
    conn = get_db()
    cur = conn.cursor()
    # Table préfixée "trading_" pour ne jamais entrer en collision avec les
    # tables d'un autre projet (ex: Pronostics) si la même base est réutilisée.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trading_users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            referrer_id BIGINT,
            referrals_count INT DEFAULT 0,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def add_user(user_id: int, username: str, referrer_id: int = None) -> bool:
    """Enregistre l'utilisateur s'il n'existe pas déjà. Retourne True si c'est
    une toute nouvelle inscription (pour ne compter un parrainage qu'une fois)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM trading_users WHERE user_id = %s", (user_id,))
    exists = cur.fetchone() is not None

    if not exists:
        cur.execute(
            "INSERT INTO trading_users (user_id, username, referrer_id) VALUES (%s, %s, %s)",
            (user_id, username, referrer_id)
        )
        if referrer_id:
            cur.execute(
                "UPDATE trading_users SET referrals_count = referrals_count + 1 WHERE user_id = %s",
                (referrer_id,)
            )
    else:
        cur.execute("UPDATE trading_users SET last_active = CURRENT_TIMESTAMP WHERE user_id = %s", (user_id,))

    conn.commit()
    cur.close()
    conn.close()
    return not exists


def get_all_user_ids():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM trading_users")
    ids = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return ids


def get_all_users_detailed():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, referrals_count FROM trading_users ORDER BY joined_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_user_referrals(user_id: int) -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT referrals_count FROM trading_users WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else 0


# --- VÉRIFICATION DE SIGNATURE TELEGRAM (sécurité des endpoints admin) ---
def verify_telegram_data(init_data: str):
    """Vérifie la signature HMAC de Telegram et retourne l'utilisateur vérifié,
    ou None si la signature est invalide. Ne jamais faire confiance à un ID
    envoyé tel quel par le client pour une action sensible (ex: diffusion, admin)."""
    if not init_data or not TOKEN:
        return None
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated_hash, received_hash):
            return None
        return json.loads(parsed.get("user", "{}"))
    except Exception:
        return None


# --- ANALYSE TECHNIQUE RÉELLE (EMA + RSI) — PLUS DE NOMBRE ALÉATOIRE ---
PAIRS_CONFIG = {
    "BTCUSDT": {"type": "crypto", "symbol": "BTCUSDT", "decimals": 2, "unit": "USDT"},
    "ETHUSDT": {"type": "crypto", "symbol": "ETHUSDT", "decimals": 2, "unit": "USDT"},
    "SOLUSDT": {"type": "crypto", "symbol": "SOLUSDT", "decimals": 2, "unit": "USDT"},
    "EURUSD": {"type": "forex", "symbol": "EURUSD", "decimals": 5, "unit": "$"},
    "GBPUSD": {"type": "forex", "symbol": "GBPUSD", "decimals": 5, "unit": "$"},
    "USDJPY": {"type": "forex", "symbol": "USDJPY", "decimals": 3, "unit": "¥"}
}

INTERVAL_MAP = {"1": "1m", "5": "5m", "15": "15m", "60": "1h"}


def fetch_real_price(symbol_key):
    config = PAIRS_CONFIG.get(symbol_key, PAIRS_CONFIG["BTCUSDT"])
    if config["type"] == "crypto":
        try:
            url = f"https://api.binance.com/api/v3/ticker/price?symbol={config['symbol']}"
            res = requests.get(url, timeout=5).json()
            if "price" in res:
                return float(res["price"])
        except Exception as e:
            logger.error(f"Erreur récupération prix Binance {symbol_key}: {e}")
    fallback_prices = {"BTCUSDT": 76192.35, "ETHUSDT": 2650.40, "SOLUSDT": 188.50,
                        "EURUSD": 1.0852, "GBPUSD": 1.2940, "USDJPY": 153.20}
    return fallback_prices.get(symbol_key, 100.0)


def compute_crypto_signal(symbol_key, tf):
    """Signal basé sur un vrai croisement EMA 9/21 + RSI 14, calculé sur les
    bougies réelles de Binance. Pas de génération aléatoire."""
    config = PAIRS_CONFIG[symbol_key]
    interval = INTERVAL_MAP.get(tf, "15m")
    url = f"https://api.binance.com/api/v3/klines?symbol={config['symbol']}&interval={interval}&limit=100"
    res = requests.get(url, timeout=8).json()
    closes = [float(k[4]) for k in res]
    series = pd.Series(closes)

    ema_fast = EMAIndicator(series, window=9).ema_indicator()
    ema_slow = EMAIndicator(series, window=21).ema_indicator()
    rsi = RSIIndicator(series, window=14).rsi()

    last_price = closes[-1]
    ema_diff = ema_fast.iloc[-1] - ema_slow.iloc[-1]
    rsi_val = rsi.iloc[-1]

    is_buy = ema_diff > 0

    # Confiance dérivée de la force du croisement EMA + de l'écart du RSI par
    # rapport à sa zone neutre (50) — plafonnée pour rester honnête, jamais
    # présentée comme une certitude.
    ema_strength = min(abs(ema_diff) / last_price * 1000, 1.0)
    rsi_strength = min(abs(rsi_val - 50) / 50, 1.0)
    confidence = 50 + round((ema_strength * 0.6 + rsi_strength * 0.4) * 42)
    confidence = max(50, min(confidence, 92))

    return is_buy, confidence, last_price


def compute_forex_signal(symbol_key):
    """Forex : les données intrajournalières gratuites sont limitées. On
    calcule une tendance réelle (SMA courte vs longue) sur l'historique
    quotidien EUR-based de frankfurter.app, avec une confiance plus prudente
    qu'en crypto puisque la résolution est journalière, pas intraday."""
    try:
        res = requests.get(
            "https://api.frankfurter.app/2024-01-01..?from=EUR&to=USD,GBP,JPY",
            timeout=8
        ).json()
        rates = res.get("rates", {})
        dates = sorted(rates.keys())[-20:]
        eur_usd = [rates[d]["USD"] for d in dates]
        eur_gbp = [rates[d]["GBP"] for d in dates]
        eur_jpy = [rates[d]["JPY"] for d in dates]

        if symbol_key == "EURUSD":
            series_vals = eur_usd
        elif symbol_key == "GBPUSD":
            series_vals = [u / g for u, g in zip(eur_usd, eur_gbp)]
        else:  # USDJPY
            series_vals = [j / u for j, u in zip(eur_jpy, eur_usd)]

        series = pd.Series(series_vals)
        sma_short = series.rolling(5).mean().iloc[-1]
        sma_long = series.rolling(15).mean().iloc[-1]
        last_price = series_vals[-1]

        is_buy = sma_short > sma_long
        diff_pct = abs(sma_short - sma_long) / last_price
        confidence = 50 + round(min(diff_pct * 2000, 1.0) * 30)  # plus prudent qu'en crypto
        confidence = max(50, min(confidence, 80))
        return is_buy, confidence, last_price
    except Exception as e:
        logger.error(f"Erreur signal forex {symbol_key}: {e}")
        price = fetch_real_price(symbol_key)
        return True, 50, price


def generate_signal(symbol, tf):
    config = PAIRS_CONFIG.get(symbol, PAIRS_CONFIG["BTCUSDT"])
    if config["type"] == "crypto":
        is_buy, confidence, price = compute_crypto_signal(symbol, tf)
    else:
        is_buy, confidence, price = compute_forex_signal(symbol)

    decimals = config["decimals"]
    unit = config["unit"]
    tf_multipliers = {"1": 0.002, "5": 0.005, "15": 0.009, "60": 0.018}
    mult = tf_multipliers.get(str(tf), 0.009)

    if is_buy:
        tp1 = round(price * (1 + mult), decimals)
        tp2 = round(price * (1 + mult * 1.8), decimals)
        sl = round(price * (1 - mult * 0.8), decimals)
        dir_text = "BUY / ACHAT"
    else:
        tp1 = round(price * (1 - mult), decimals)
        tp2 = round(price * (1 - mult * 1.8), decimals)
        sl = round(price * (1 + mult * 0.8), decimals)
        dir_text = "SELL / VENTE"

    fmt = f"%.{decimals}f"
    return {
        "success": True,
        "symbol": symbol,
        "timeframe": str(tf),
        "probability": confidence,
        "direction": dir_text,
        "isBuy": is_buy,
        "unit": unit,
        "entryPrice": f"{fmt % price} {unit}",
        "tp1": f"{fmt % tp1} {unit}",
        "tp2": f"{fmt % tp2} {unit}",
        "sl": f"{fmt % sl} {unit}",
        "disclaimer": "Signal indicatif basé sur une analyse technique réelle (EMA/RSI). "
                       "Ne constitue pas un conseil en investissement. Le trading comporte un risque de perte en capital."
    }


# --- BOT TELEGRAM ---
if bot:
    @bot.message_handler(commands=["start"])
    def start_cmd(msg):
        u_id = int(msg.from_user.id)
        u_name = msg.from_user.username or "Utilisateur"

        args = msg.text.split()
        ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() and int(args[1]) != u_id else None

        add_user(u_id, u_name, ref_id)

        markup = InlineKeyboardMarkup()
        user_app_url = f"{URL_MINI_APP}?uid={u_id}"
        markup.add(InlineKeyboardButton("🚀 Commencer", web_app=WebAppInfo(url=user_app_url)))

        text = (
            f"👋 **Bienvenue {u_name} !**\n\n"
            "Accédez gratuitement à nos signaux Forex & Crypto basés sur une analyse technique en temps réel.\n\n"
            "👉 Cliquez sur **Commencer** ci-dessous."
        )
        bot.send_message(msg.chat.id, text, reply_markup=markup)

    @bot.message_handler(commands=["admin"])
    def admin_cmd(msg):
        if int(msg.from_user.id) != ADMIN_ID or ADMIN_ID == 0:
            return

        users = get_all_users_detailed()
        header = f"⚙️ **PANNEAU D'ADMINISTRATION**\n\n👥 Total utilisateurs : **{len(users)}**\n\n"
        bot.send_message(msg.chat.id, header)

        if not users:
            return

        # Découpage en plusieurs messages si la liste est longue (limite Telegram ~4096 caractères)
        chunk = ""
        for user_id, username, ref_count in users:
            uname = f"@{username}" if username else "(pas de username)"
            line = f"• {user_id} — {uname} — {ref_count} filleul(s)\n"
            if len(chunk) + len(line) > 3500:
                bot.send_message(msg.chat.id, chunk)
                chunk = ""
            chunk += line
        if chunk:
            bot.send_message(msg.chat.id, chunk)

    @bot.message_handler(commands=["broadcast"])
    def broadcast_start(msg):
        if int(msg.from_user.id) != ADMIN_ID or ADMIN_ID == 0:
            return
        sent = bot.reply_to(
            msg,
            "📢 Envoyez-moi maintenant le contenu à diffuser — texte, photo, vidéo, audio, note vocale, "
            "note vidéo (cercle) ou fichier. Je reconnais automatiquement le type, pas besoin de le préciser."
        )
        bot.register_next_step_handler(sent, broadcast_receive)

    def broadcast_receive(msg):
        if int(msg.from_user.id) != ADMIN_ID or ADMIN_ID == 0:
            return

        user_ids = get_all_user_ids()
        success, failed = 0, 0
        bot.send_message(msg.chat.id, f"⏳ Diffusion auprès de {len(user_ids)} utilisateurs...")

        for uid in user_ids:
            try:
                ct = msg.content_type
                if ct == "text":
                    bot.send_message(uid, msg.text)
                elif ct == "photo":
                    bot.send_photo(uid, msg.photo[-1].file_id, caption=msg.caption)
                elif ct == "video":
                    bot.send_video(uid, msg.video.file_id, caption=msg.caption)
                elif ct == "audio":
                    bot.send_audio(uid, msg.audio.file_id, caption=msg.caption)
                elif ct == "voice":
                    bot.send_voice(uid, msg.voice.file_id, caption=msg.caption)
                elif ct == "video_note":
                    bot.send_video_note(uid, msg.video_note.file_id)
                elif ct == "document":
                    bot.send_document(uid, msg.document.file_id, caption=msg.caption)
                else:
                    continue
                success += 1
            except Exception as e:
                failed += 1
                logger.warning(f"Échec diffusion vers {uid} : {e}")

        bot.send_message(msg.chat.id, f"✅ Diffusion terminée !\n• Reçus : {success}\n• Échecs : {failed}")


def send_market_alerts():
    """Publie une alerte marché avec notre logo dans le canal, basée sur le
    même calcul technique réel que la Mini App — plus de tirage aléatoire."""
    if not bot or not CHANNEL_ID:
        return
    try:
        pairs = ["BTCUSDT", "ETHUSDT", "EURUSD"]
        selected = random.choice(pairs)  # on choisit juste QUEL actif commenter, pas le sens du signal
        is_buy, confidence, price = generate_signal_raw(selected)
        config = PAIRS_CONFIG[selected]
        direction = "HAUSSIER 🚀" if is_buy else "BAISSIER 📉"

        caption = (
            f"🚨 **ALERTE MARCHÉ**\n\n"
            f"📊 Actif : `{selected}`\n"
            f"💰 Prix actuel : `{price} {config['unit']}`\n"
            f"📈 Tendance (analyse technique) : {direction} — confiance {confidence}%\n\n"
            f"⚠️ _Signal indicatif, ne constitue pas un conseil en investissement._\n"
            f"👉 Consultez la Mini App pour le détail (entrée, TP, SL)."
        )
        markup = InlineKeyboardMarkup()
        if BOT_USERNAME:
            markup.add(InlineKeyboardButton("📈 Ouvrir la Mini App", url=f"https://t.me/{BOT_USERNAME}"))

        if os.path.exists(LOGO_PATH):
            with open(LOGO_PATH, "rb") as f:
                bot.send_photo(CHANNEL_ID, f, caption=caption, reply_markup=markup)
        else:
            bot.send_message(CHANNEL_ID, caption, reply_markup=markup)
        logger.info(f"Alerte marché envoyée dans {CHANNEL_ID}")
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi de l'alerte marché : {e}")


def generate_signal_raw(symbol_key):
    config = PAIRS_CONFIG[symbol_key]
    if config["type"] == "crypto":
        return compute_crypto_signal(symbol_key, "15")
    return compute_forex_signal(symbol_key)


sched = BackgroundScheduler(daemon=True)
# Alerte automatique dans le canal toutes les 30 minutes (aucun rappel de
# relance n'est envoyé aux utilisateurs — cette fonctionnalité a été retirée).
sched.add_job(send_market_alerts, 'interval', minutes=30)
sched.start()


# --- API WEB (FLASK) ---
app = Flask(__name__)
CORS(app)


@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
@app.route("/ping", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "Trading Bot Web Service"}), 200


@app.route("/api/generate-signal", methods=["POST"])
def api_generate_signal():
    data = request.json or {}
    symbol = data.get("symbol", "BTCUSDT").upper()
    tf = str(data.get("timeframe", "15"))
    try:
        return jsonify(generate_signal(symbol, tf))
    except Exception as e:
        logger.error(f"Erreur génération signal {symbol}: {e}")
        return jsonify({"success": False, "error": "Erreur lors du calcul du signal."}), 500


@app.route("/api/user-status", methods=["POST"])
def user_status():
    data = request.json or {}
    try:
        u_id = int(data.get("userId", 0))
    except (ValueError, TypeError):
        u_id = 0

    if not u_id:
        return jsonify({"error": "userId obligatoire"}), 400

    ref_count = get_user_referrals(u_id)
    users = get_all_user_ids()

    return jsonify({
        "isAdmin": (u_id == ADMIN_ID and ADMIN_ID != 0),
        "referralsCount": ref_count,
        "stats": {"totalUsers": len(users)}
    })


@app.route("/api/admin/users", methods=["POST"])
def admin_users():
    """Liste détaillée des utilisateurs pour le panneau admin web — vérifiée
    par signature Telegram, pas par un simple ID envoyé par le client."""
    data = request.json or {}
    user = verify_telegram_data(data.get("initData", ""))
    if not user or int(user.get("id", 0)) != ADMIN_ID or ADMIN_ID == 0:
        return jsonify({"success": False, "error": "Accès refusé."}), 403

    users = get_all_users_detailed()
    return jsonify({
        "success": True,
        "users": [{"userId": u[0], "username": u[1], "referrals": u[2]} for u in users]
    })


if __name__ == "__main__":
    init_db()
    t = Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000))))
    t.daemon = True
    t.start()
    logger.info("⚡ Web Service Flask démarré !")
    if bot:
        logger.info("🤖 Bot Telegram démarré !")
        bot.infinity_polling(none_stop=True, skip_pending=True)
        
