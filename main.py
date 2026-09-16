import os
import sys
import logging
import sqlite3
import random
from datetime import datetime, timedelta
from threading import Thread

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TradingBot")

TOKEN = os.environ.get("BOT_TOKEN", "").strip() or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "@trading_pronos").strip()
ADMIN_ID_RAW = os.environ.get("ADMIN_ID", "0").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown") if TOKEN else None
URL_MINI_APP = os.environ.get("URL_MINI_APP_TRADING", "https://trading-3wcr.onrender.com")

EXNESS_LINK = "https://one.exnessonelink.com/a/395vyusacl"
KUCOIN_LINK = "https://www.kucoin.com/ucenter/signup?&rcode=rEN8V1E&utm_medium=U17710"

user_states = {}
user_temp_data = {}

DB_FILE = "bot_database.db"

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                status TEXT DEFAULT 'PENDING',
                funnel_step TEXT DEFAULT 'STARTED',
                vip_expiry TEXT,
                referrer_id INTEGER,
                referrals_count INTEGER DEFAULT 0,
                last_active TEXT,
                linked_account TEXT DEFAULT NULL,
                last_reminder_sent TEXT DEFAULT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_validations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                account_id TEXT,
                photo_id TEXT,
                status TEXT DEFAULT 'PENDING',
                created_at TEXT
            )
        """)
        conn.commit()

init_db()

def update_user_activity(user_id: int, step: str = None):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        c = conn.cursor()
        if step:
            c.execute("UPDATE users SET last_active = ?, funnel_step = ? WHERE user_id = ?", (now_str, step, user_id))
        else:
            c.execute("UPDATE users SET last_active = ? WHERE user_id = ?", (now_str, user_id))
        conn.commit()

def check_channel_membership(user_id: int) -> bool:
    if not CHANNEL_ID or not bot or user_id == ADMIN_ID:
        return True
    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["creator", "administrator", "member"]
    except Exception as e:
        logger.error(f"Erreur vérification canal : {e}")
        return False

# --- LOGIQUE D'ANALYSE DES PRIX EN TEMPS RÉEL ET IA ---
PAIRS_CONFIG = {
    "BTCUSDT": {"type": "crypto", "symbol": "BTCUSDT", "decimals": 2, "unit": "USDT"},
    "ETHUSDT": {"type": "crypto", "symbol": "ETHUSDT", "decimals": 2, "unit": "USDT"},
    "SOLUSDT": {"type": "crypto", "symbol": "SOLUSDT", "decimals": 2, "unit": "USDT"},
    "EURUSD": {"type": "forex", "symbol": "EURUSDT", "decimals": 5, "unit": "$"},
    "GBPUSD": {"type": "forex", "symbol": "GBPUSDT", "decimals": 5, "unit": "$"},
    "USDJPY": {"type": "forex", "symbol": "USDJPY", "decimals": 3, "unit": "¥"}
}

def fetch_real_price(symbol_key):
    config = PAIRS_CONFIG.get(symbol_key, PAIRS_CONFIG["BTCUSDT"])
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={config['symbol']}"
        res = requests.get(url, timeout=5).json()
        if "price" in res:
            return float(res["price"])
    except Exception as e:
        logger.error(f"Erreur récupération prix Binance {symbol_key}: {e}")

    # Backup Forex
    if config["type"] == "forex":
        try:
            f_res = requests.get("https://api.frankfurter.app/latest?from=EUR&to=USD", timeout=5).json()
            if symbol_key == "EURUSD":
                return float(f_res["rates"]["USD"])
        except Exception:
            pass

    fallback_prices = {"BTCUSDT": 76192.35, "ETHUSDT": 2650.40, "SOLUSDT": 188.50, "EURUSD": 1.0852, "GBPUSD": 1.2940, "USDJPY": 153.20}
    return fallback_prices.get(symbol_key, 100.0)

if bot:
    @bot.message_handler(commands=["start"])
    def start_cmd(msg):
        u_id = int(msg.from_user.id)
        u_name = msg.from_user.username or "Utilisateur"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        args = msg.text.split()
        ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() and int(args[1]) != u_id else None

        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT status FROM users WHERE user_id = ?", (u_id,))
            row = c.fetchone()
            if not row:
                c.execute(
                    "INSERT INTO users (user_id, username, status, funnel_step, referrer_id, last_active) VALUES (?, ?, 'PENDING', 'STARTED', ?, ?)",
                    (u_id, u_name, ref_id, now_str)
                )
                if ref_id:
                    c.execute("UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id = ?", (ref_id,))
            else:
                c.execute("UPDATE users SET last_active = ? WHERE user_id = ?", (now_str, u_id))
            conn.commit()

        if not check_channel_membership(u_id):
            markup = InlineKeyboardMarkup()
            clean_channel = CHANNEL_ID.replace("@", "")
            markup.add(InlineKeyboardButton("📢 Rejoindre le Canal Officiel", url=f"https://t.me/{clean_channel}"))
            markup.add(InlineKeyboardButton("✅ J'ai rejoint le canal", callback_data="check_join"))
            bot.reply_to(msg, "🔒 **ACCÈS RESTREINT**\n\nVous devez obligatoirement rejoindre notre canal officiel pour utiliser le bot.", reply_markup=markup)
            return

        send_main_menu(msg.chat.id, u_name, u_id)

    @bot.callback_query_handler(func=lambda call: call.data == "check_join")
    def callback_check_join(call):
        u_id = int(call.from_user.id)
        if check_channel_membership(u_id):
            bot.answer_callback_query(call.id, "✅ Accès validé !")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            send_main_menu(call.message.chat.id, call.from_user.username or "Utilisateur", u_id)
        else:
            bot.answer_callback_query(call.id, "❌ Vous n'avez pas encore rejoint le canal !", show_alert=True)

    def send_main_menu(chat_id, u_name, u_id):
        is_verified = False
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT status FROM users WHERE user_id = ?", (int(u_id),))
            row = c.fetchone()
            if row and (row["status"] == "VERIFIED" or int(u_id) == ADMIN_ID):
                is_verified = True

        markup = InlineKeyboardMarkup(row_width=1)
        user_app_url = f"{URL_MINI_APP}?uid={u_id}"

        if is_verified:
            markup.add(InlineKeyboardButton("📈 Ouvrir la Mini App Trading", web_app=telebot.types.WebAppInfo(url=user_app_url)))
            text = (
                f"Bienvenue *{u_name}* sur le Terminal de Trading !\n\n"
                "✅ **Votre compte est vérifié.** Vous pouvez accéder gratuitement à tous les signaux et fonctionnalités de la Mini App ci-dessous :"
            )
        else:
            markup.add(InlineKeyboardButton("📊 Inscription Exness (Code Promo: 395vyusacl)", url=EXNESS_LINK))
            markup.add(InlineKeyboardButton("📊 Inscription KuCoin (Code Promo: rEN8V1E)", url=KUCOIN_LINK))
            markup.add(InlineKeyboardButton("📥 Preuves d'inscription (ID + Capture d'écran)", callback_data="submit_proof"))

            text = (
                f"Bienvenue *{u_name}* sur l'IA des Signaux Trading Crypto & Forex gratuits.\n\n"
                "🔹 **Accès à la Mini App :** Pour débloquer votre accès complet et gratuit aux signaux IA, Veuillez vous inscrire sur KuCoin ou Exness via nos liens ci-dessous, Effectuez un dépôt minimum de **10$** et Soumettez vos preuves d'inscription ici pour la validation.\n\n"
                "⚠️ *Votre compte sera validé et votre accès débloqué par un administrateur après la vérification de vos preuves.*"
            )

        bot.send_message(chat_id, text, reply_markup=markup)

    @bot.message_handler(commands=["admin"])
    def admin_cmd(msg):
        if int(msg.from_user.id) != ADMIN_ID and ADMIN_ID != 0:
            bot.reply_to(msg, "❌ Accès refusé.")
            return

        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) as total FROM users")
            total = c.fetchone()["total"]
            c.execute("SELECT COUNT(*) as verified FROM users WHERE status = 'VERIFIED'")
            verified = c.fetchone()["verified"]
            c.execute("SELECT user_id, username, status FROM users ORDER BY last_active DESC LIMIT 10")
            recent_users = c.fetchall()

        text = f"⚙️ **PANNEAU D'ADMINISTRATION**\n\n"
        text += f"👥 **Total Utilisateurs :** `{total}`\n"
        text += f"✅ **Membres Validés :** `{verified}`\n\n"
        text += "📜 **Derniers utilisateurs enregistrés :**\n"

        for u in recent_users:
            st = "✅ Validé" if u["status"] == "VERIFIED" else "⏳ En attente"
            text += f"• `{u['user_id']}` (@{u['username'] or 'sans_pseudo'}) - {st}\n"

        text += "\n*Gestion manuelle :*\n`/grant <user_id>` : Valider\n`/revoke <user_id>` : Bloquer"
        bot.send_message(msg.chat.id, text)

    @bot.message_handler(commands=["grant"])
    def grant_access(msg):
        if int(msg.from_user.id) != ADMIN_ID and ADMIN_ID != 0: return
        try:
            target_id = int(msg.text.split()[1])
            with get_db() as conn:
                conn.cursor().execute("UPDATE users SET status = 'VERIFIED', linked_account = 'MANUAL', funnel_step = 'COMPLETED' WHERE user_id = ?", (target_id,))
                conn.commit()
            bot.reply_to(msg, f"✅ L'utilisateur `{target_id}` a été validé.")

            try:
                user_app_url = f"{URL_MINI_APP}?uid={target_id}"
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("📈 Ouvrir la Mini App Trading", web_app=telebot.types.WebAppInfo(url=user_app_url)))
                bot.send_message(target_id, "🎉 **Accès Débloqué !** Votre compte a été validé.", reply_markup=markup)
            except Exception as e:
                logger.error(f"Erreur notification {target_id}: {e}")
        except Exception:
            bot.reply_to(msg, "❌ Format : `/grant 12345678`")

    @bot.message_handler(commands=["revoke"])
    def revoke_access(msg):
        if int(msg.from_user.id) != ADMIN_ID and ADMIN_ID != 0: return
        try:
            target_id = int(msg.text.split()[1])
            with get_db() as conn:
                conn.cursor().execute("UPDATE users SET status = 'PENDING', linked_account = NULL, funnel_step = 'STARTED' WHERE user_id = ?", (target_id,))
                conn.commit()
            bot.reply_to(msg, f"🚫 Accès révoqué pour `{target_id}`.")
        except Exception:
            bot.reply_to(msg, "❌ Format : `/revoke 12345678`")

    @bot.callback_query_handler(func=lambda c: c.data == "submit_proof")
    def submit_proof_start(call):
        u_id = call.message.chat.id
        user_states[u_id] = "WAIT_ID"
        update_user_activity(u_id, "WAIT_ID")
        bot.answer_callback_query(call.id)
        bot.send_message(u_id, "📝 **Étape 1/2 :** Entrez votre ID de compte Exness ou KuCoin :")

    @bot.message_handler(func=lambda m: user_states.get(m.chat.id) == "WAIT_ID")
    def process_proof_id(msg):
        u_id = msg.chat.id
        user_temp_data[u_id] = {"account_id": msg.text.strip()}
        user_states[u_id] = "WAIT_PHOTO"
        update_user_activity(u_id, "WAIT_PHOTO")
        bot.reply_to(msg, "📸 **Étape 2/2 :** Envoyez la capture d'écran de votre dépôt de 10$ minimum.")

    @bot.message_handler(content_types=["photo"], func=lambda m: user_states.get(m.chat.id) == "WAIT_PHOTO")
    def process_proof_photo(msg):
        chat_id = msg.chat.id
        u_id = int(msg.from_user.id)
        photo_id = msg.photo[-1].file_id
        acc_id = user_temp_data.get(chat_id, {}).get("account_id", "Non spécifié")

        with get_db() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO pending_validations (user_id, account_id, photo_id, created_at) VALUES (?, ?, ?, ?)",
                      (u_id, acc_id, photo_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            req_id = c.lastrowid
            c.execute("UPDATE users SET funnel_step = 'PROOFS_SENT', last_active = ? WHERE user_id = ?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), u_id))
            conn.commit()

        user_states[chat_id] = None
        bot.reply_to(msg, "✅ **Preuve reçue.** L'administrateur va vérifier votre demande.")

        if ADMIN_ID != 0:
            mk = InlineKeyboardMarkup()
            mk.add(
                InlineKeyboardButton("✅ Valider l'accès", callback_data=f"adm_ok_{req_id}"),
                InlineKeyboardButton("❌ Rejeter", callback_data=f"adm_no_{req_id}")
            )
            bot.send_photo(ADMIN_ID, photo_id, caption=f"🔔 **DEMANDE DE VALIDATION**\nUser ID: `{u_id}`\nID Compte: `{acc_id}`", reply_markup=mk)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_ok_"))
    def admin_approve(call):
        if int(call.from_user.id) != ADMIN_ID and ADMIN_ID != 0: return
        req_id = int(call.data.replace("adm_ok_", ""))
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT user_id, account_id FROM pending_validations WHERE id = ?", (req_id,))
            row = c.fetchone()
            if row:
                u_id = int(row["user_id"])
                c.execute("UPDATE users SET status = 'VERIFIED', linked_account = ?, funnel_step = 'COMPLETED' WHERE user_id = ?", (str(row["account_id"]), u_id))
                c.execute("UPDATE pending_validations SET status = 'APPROVED' WHERE id = ?", (req_id,))
                conn.commit()
                bot.edit_message_caption(caption="✅ **Demande Approuvée avec succès.**", chat_id=call.message.chat.id, message_id=call.message.message_id)

                try:
                    user_app_url = f"{URL_MINI_APP}?uid={u_id}"
                    mk = InlineKeyboardMarkup()
                    mk.add(InlineKeyboardButton("📈 Ouvrir la Mini App Trading", web_app=telebot.types.WebAppInfo(url=user_app_url)))
                    bot.send_message(u_id, "🎉 **Félicitations ! Votre compte a été validé !**", reply_markup=mk)
                except Exception as e:
                    logger.error(f"Erreur notification {u_id}: {e}")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_no_"))
    def admin_reject(call):
        if int(call.from_user.id) != ADMIN_ID and ADMIN_ID != 0: return
        req_id = int(call.data.replace("adm_no_", ""))
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT user_id FROM pending_validations WHERE id = ?", (req_id,))
            row = c.fetchone()
            if row:
                u_id = int(row["user_id"])
                c.execute("UPDATE pending_validations SET status = 'REJECTED' WHERE id = ?", (req_id,))
                c.execute("UPDATE users SET funnel_step = 'STARTED' WHERE user_id = ?", (u_id,))
                conn.commit()
                bot.edit_message_caption(caption="❌ **Demande Rejetée.**", chat_id=call.message.chat.id, message_id=call.message.message_id)

def run_reminders_3h():
    if not bot: return
    limit_time = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT user_id FROM users 
            WHERE status = 'PENDING' 
              AND funnel_step IN ('STARTED', 'WAIT_ID', 'WAIT_PHOTO') 
              AND last_active <= ? 
              AND last_reminder_sent IS NULL
        """, (limit_time,))
        users = c.fetchall()
        for u in users:
            try:
                mk = InlineKeyboardMarkup(row_width=1)
                mk.add(InlineKeyboardButton("📥 Envoyer Preuves (10$)", callback_data="submit_proof"))
                bot.send_message(int(u["user_id"]), "⏰ **RAPPEL : Finalisez votre accès au Terminal IA !**", reply_markup=mk)
                c.execute("UPDATE users SET last_reminder_sent = ? WHERE user_id = ?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), int(u["user_id"])))
            except Exception:
                pass
        conn.commit()

# --- NOUVELLE FONCTION : ENVOI D'ALERTES DE PRIX DANS LE CANAL ---
def send_market_alerts():
    if not bot or not CHANNEL_ID:
        return

    pairs = ["BTCUSDT", "ETHUSDT", "EURUSD"]
    selected = random.choice(pairs)
    config = PAIRS_CONFIG[selected]
    price = fetch_real_price(selected)
    direction = "BULLISH 🚀" if random.random() > 0.5 else "BEARISH 📉"

    msg = (
        f"🚨 **ALERTE MARCHÉ EN TEMPS RÉEL**\n\n"
        f"📊 **Actif :** `{selected}`\n"
        f"💰 **Prix actuel :** `{price} {config['unit']}`\n"
        f"📈 **Tendance détectée :** {direction}\n\n"
        f"👉 _Consultez la Mini App pour obtenir le signal d'entrée exact avec TP et SL._"
    )

    try:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📈 Analyser dans la Mini App", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}"))
        bot.send_message(CHANNEL_ID, msg, reply_markup=markup)
        logger.info(f"Alerte marché envoyée dans {CHANNEL_ID}")
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi de l'alerte marché : {e}")

sched = BackgroundScheduler(daemon=True)
sched.add_job(run_reminders_3h, 'interval', minutes=15)
# Envoie une alerte automatique dans le canal toutes les 30 minutes
sched.add_job(send_market_alerts, 'interval', minutes=30)
sched.start()

app = Flask(__name__)
CORS(app)

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
@app.route("/ping", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "Trading Bot Web Service"}), 200

# ROUTE GÉNÉRATEUR DE SIGNAUX IA EN TEMPS RÉEL
@app.route("/api/generate-signal", methods=["POST"])
def api_generate_signal():
    data = request.json or {}
    symbol = data.get("symbol", "BTCUSDT").upper()
    tf = str(data.get("timeframe", "15"))

    config = PAIRS_CONFIG.get(symbol, PAIRS_CONFIG["BTCUSDT"])
    price = fetch_real_price(symbol)
    decimals = config["decimals"]
    unit = config["unit"]

    direction = "BUY" if random.random() > 0.45 else "SELL"
    probability = random.randint(82, 96)

    tf_multipliers = {"1": 0.002, "5": 0.005, "15": 0.009, "60": 0.018}
    mult = tf_multipliers.get(tf, 0.009)

    if direction == "BUY":
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

    return jsonify({
        "success": True,
        "symbol": symbol,
        "timeframe": tf,
        "probability": probability,
        "direction": dir_text,
        "isBuy": direction == "BUY",
        "unit": unit,
        "entryPrice": f"{fmt % price} {unit}",
        "tp1": f"{fmt % tp1} {unit}",
        "tp2": f"{fmt % tp2} {unit}",
        "sl": f"{fmt % sl} {unit}"
    })

@app.route("/api/user-status", methods=["POST"])
def user_status():
    data = request.json or {}
    try:
        u_id = int(data.get("userId", 0))
    except (ValueError, TypeError):
        u_id = 0

    if not u_id:
        return jsonify({"error": "userId obligatoire"}), 400

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT status, linked_account, referrals_count FROM users WHERE user_id = ?", (u_id,))
        row = c.fetchone()

        c.execute("SELECT COUNT(*) as total FROM users")
        total_users = c.fetchone()["total"]
        c.execute("SELECT COUNT(*) as verified FROM users WHERE status = 'VERIFIED'")
        verified_users = c.fetchone()["verified"]

        is_verified = False
        linked = None
        ref_count = 0

        if row:
            linked = row["linked_account"]
            ref_count = row["referrals_count"] or 0
            if row["status"] == "VERIFIED":
                is_verified = True

        if u_id == ADMIN_ID and ADMIN_ID != 0:
            is_verified = True

        return jsonify({
            "isVip": is_verified,
            "isVerified": is_verified,
            "isAdmin": (u_id == ADMIN_ID and ADMIN_ID != 0),
            "isLinked": bool(linked or is_verified),
            "linkedAccount": linked,
            "referralsCount": ref_count,
            "stats": {
                "totalUsers": total_users,
                "vipUsers": verified_users
            }
        })

@app.route("/api/admin/toggle-vip", methods=["POST"])
def admin_toggle_vip():
    data = request.json or {}
    try:
        admin_id = int(data.get("adminId", 0))
        target_id = int(data.get("targetId", 0))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "IDs invalides"}), 400

    action = data.get("action")
    if admin_id != ADMIN_ID and ADMIN_ID != 0:
        return jsonify({"success": False, "error": "Accès refusé"}), 403

    with get_db() as conn:
        c = conn.cursor()
        if action == "grant":
            c.execute("UPDATE users SET status = 'VERIFIED', linked_account = 'MANUAL', funnel_step = 'COMPLETED' WHERE user_id = ?", (target_id,))
            conn.commit()
            return jsonify({"success": True, "message": f"Accès accordé à {target_id}."})
        elif action == "revoke":
            c.execute("UPDATE users SET status = 'PENDING', linked_account = NULL, funnel_step = 'STARTED' WHERE user_id = ?", (target_id,))
            conn.commit()
            return jsonify({"success": True, "message": f"Accès révoqué pour {target_id}."})

    return jsonify({"success": False, "error": "Action invalide"}), 400

if __name__ == "__main__":
    t = Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000))))
    t.daemon = True
    t.start()
    logger.info("⚡ Web Service Flask démarré !")
    if bot:
        logger.info("🤖 Bot Telegram démarré !")
        bot.infinity_polling(none_stop=True, skip_pending=True)
