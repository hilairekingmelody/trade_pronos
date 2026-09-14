import os
import sys
import time
import logging
import sqlite3
from datetime import datetime, timedelta
from threading import Thread

from flask import Flask, jsonify, request
import requests
import telebot
from telebot.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
from apscheduler.schedulers.background import BackgroundScheduler

# ---------------------------------------------------------
# 1. CONFIGURATION & SÉCURITÉ
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("MegaBot")

TOKEN = os.environ.get("BOT_TOKEN", "").strip() or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()

ADMIN_ID_RAW = os.environ.get("ADMIN_ID", "0").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0

if not TOKEN:
    logger.warning("⚠️ BOT_TOKEN manquant dans l'environnement ! Vérifiez vos variables.")

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown") if TOKEN else None

user_states = {}
user_temp_data = {}

URL_MINI_APP_TRADING = os.environ.get("URL_MINI_APP_TRADING", "https://t.me/ton_bot/trading_app")

# ---------------------------------------------------------
# 2. BASE DE DONNÉES SQLITE
# ---------------------------------------------------------
DB_FILE = "bot_database.db"

def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                chosen_universe TEXT DEFAULT 'trading',
                status TEXT DEFAULT 'FREE',
                funnel_step TEXT DEFAULT 'STARTED',
                vip_expiry TEXT,
                referrer_id INTEGER,
                referrals_count INTEGER DEFAULT 0,
                last_active TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_validations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                universe TEXT,
                platform TEXT,
                account_id TEXT,
                photo_id TEXT,
                status TEXT DEFAULT 'PENDING',
                created_at TEXT
            )
        """)
        conn.commit()

init_db()

# ---------------------------------------------------------
# 3. VERIFICATION FORCE JOIN (CANAL)
# ---------------------------------------------------------
def check_channel_membership(user_id: int) -> bool:
    if not CHANNEL_ID or not bot or user_id == ADMIN_ID:
        return True
    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["creator", "administrator", "member"]
    except Exception as e:
        logger.error(f"Erreur contrôle canal : {e}")
        return True

# ---------------------------------------------------------
# 4. PARCOURS UTILISATEUR & COMMANDES
# ---------------------------------------------------------
if bot:
    @bot.message_handler(commands=["start"])
    def start_cmd(msg):
        user_id = msg.from_user.id
        username = msg.from_user.username or "Inconnu"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        args = msg.text.split()
        referrer_id = int(args[1]) if len(args) > 1 and args[1].isdigit() and int(args[1]) != user_id else None

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, status FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()

            if not row:
                cursor.execute(
                    "INSERT INTO users (user_id, username, status, funnel_step, referrer_id, last_active) VALUES (?, ?, 'FREE', 'STARTED', ?, ?)",
                    (user_id, username, referrer_id, now_str)
                )
                if referrer_id:
                    cursor.execute("UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id = ?", (referrer_id,))
                    cursor.execute("SELECT referrals_count FROM users WHERE user_id = ?", (referrer_id,))
                    ref_row = cursor.fetchone()
                    if ref_row and ref_row["referrals_count"] % 3 == 0:
                        new_exp = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
                        cursor.execute("UPDATE users SET status = 'VIP', vip_expiry = ? WHERE user_id = ?", (new_exp, referrer_id))
                        try:
                            bot.send_message(referrer_id, "🎉 **FÉLICITATIONS !** Vous avez invité 3 amis. Vous gagnez **1 mois VIP gratuit** !")
                        except Exception:
                            pass
                conn.commit()
                status = "FREE"
            else:
                status = row["status"]
                cursor.execute("UPDATE users SET last_active = ? WHERE user_id = ?", (now_str, user_id))
                conn.commit()

        if not check_channel_membership(user_id):
            markup = InlineKeyboardMarkup()
            clean_channel = CHANNEL_ID.replace('@', '')
            markup.add(InlineKeyboardButton("📢 Rejoindre le Canal", url=f"https://t.me/{clean_channel}"))
            markup.add(InlineKeyboardButton("✅ J'ai rejoint le canal", callback_data="check_join"))
            bot.reply_to(msg, "🔒 **ACCÈS RESTREINT**\n\nVous devez obligatoirement rejoindre notre canal officiel pour continuer.", reply_markup=markup)
            return

        if status in ["VIP", "PREMIUM"] or user_id == ADMIN_ID:
            send_app_access(msg.chat.id)
            return

        send_trading_funnel(msg.chat.id)

    @bot.callback_query_handler(func=lambda call: call.data == "check_join")
    def callback_check_join(call):
        if check_channel_membership(call.from_user.id):
            bot.answer_callback_query(call.id, "✅ Accès validé !")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            send_trading_funnel(call.message.chat.id)
        else:
            bot.answer_callback_query(call.id, "❌ Vous n'avez pas encore rejoint le canal !", show_alert=True)

    def send_trading_funnel(chat_id):
        text = (
            "📊 **ACCÈS AU TERMINAL DE TRADING**\n\n"
            "Pour débloquer votre accès VIP à la Mini App, inscrivez-vous chez l'un de nos partenaires et effectuez un dépôt minimum de **10 $** :\n\n"
            "🔹 **Exness** (Forex) — Code Promo : `395vyusacl`\n"
            "👉 [S'inscrire sur Exness](https://one.exnessonelink.com/a/395vyusacl)\n\n"
            "🔹 **KuCoin** (Crypto) — Code Promo : `rEN8V1E`\n"
            "👉 [S'inscrire sur KuCoin](https://www.kucoin.com/ucenter/signup?&rcode=rEN8V1E&utm_medium=U17710)\n\n"
            "👇 *Une fois inscrit et votre dépôt fait, soumettez vos preuves :*"
        )
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📤 Envoyer mes preuves de dépôt", callback_data="submit_proof"))
        markup.add(InlineKeyboardButton("⭐ Obtenir le Pass via Telegram Stars", callback_data="pay_stars_500"))
        bot.send_message(chat_id, text, reply_markup=markup, disable_web_page_preview=True)

    # ---------------------------------------------------------
    # 5. DEMANDE & VALIDATION PROOF
    # ---------------------------------------------------------
    @bot.callback_query_handler(func=lambda call: call.data == "submit_proof")
    def start_proof_submission(call):
        chat_id = call.message.chat.id
        user_states[chat_id] = "WAITING_ID"
        user_temp_data[chat_id] = {}

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET funnel_step = 'SUBMITTING_ID' WHERE user_id = ?", (call.from_user.id,))
            conn.commit()

        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "📝 **Étape 1/2 :** Entrez votre **ID de compte** Exness ou KuCoin :")

    @bot.message_handler(func=lambda msg: user_states.get(msg.chat.id) == "WAITING_ID")
    def process_account_id(msg):
        chat_id = msg.chat.id
        user_temp_data[chat_id]["account_id"] = msg.text.strip()
        user_states[chat_id] = "WAITING_PHOTO"
        bot.reply_to(msg, "📸 **Étape 2/2 :** Envoyez la **capture d'écran** confirmant votre dépôt de 10$.")

    @bot.message_handler(content_types=["photo"], func=lambda msg: user_states.get(msg.chat.id) == "WAITING_PHOTO")
    def process_proof_photo(msg):
        chat_id = msg.chat.id
        user_id = msg.from_user.id
        photo_id = msg.photo[-1].file_id

        data = user_temp_data.get(chat_id, {})
        account_id = data.get("account_id", "Inconnu")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO pending_validations (user_id, universe, account_id, photo_id, created_at) VALUES (?, 'trading', ?, ?, ?)",
                (user_id, account_id, photo_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            req_id = cursor.lastrowid
            cursor.execute("UPDATE users SET funnel_step = 'PROOF_SUBMITTED' WHERE user_id = ?", (user_id,))
            conn.commit()

        user_states[chat_id] = None
        user_temp_data[chat_id] = None

        bot.reply_to(msg, "✅ **Preuves transmises !** L'administration va vérifier vos informations rapidement.")

        if ADMIN_ID != 0:
            markup = InlineKeyboardMarkup()
            markup.add(
                InlineKeyboardButton("✅ Valider (VIP 30J)", callback_data=f"adm_accept_{req_id}"),
                InlineKeyboardButton("❌ Rejeter", callback_data=f"adm_reject_{req_id}")
            )
            admin_text = (
                f"🔔 **DEMANDE D'ACCÈS VIP TRADING**\n\n"
                f"👤 **Utilisateur :** `{user_id}` (@{msg.from_user.username or 'Sans pseudo'})\n"
                f"🆔 **ID Compte :** `{account_id}`"
            )
            bot.send_photo(ADMIN_ID, photo_id, caption=admin_text, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_accept_"))
    def admin_accept(call):
        if call.from_user.id != ADMIN_ID:
            return

        req_id = int(call.data.replace("adm_accept_", ""))
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM pending_validations WHERE id = ?", (req_id,))
            row = cursor.fetchone()

            if row:
                target_id = row["user_id"]
                expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

                cursor.execute("UPDATE users SET status = 'VIP', vip_expiry = ?, funnel_step = 'COMPLETED' WHERE user_id = ?", (expiry, target_id))
                cursor.execute("UPDATE pending_validations SET status = 'APPROVED' WHERE id = ?", (req_id,))
                conn.commit()

                bot.answer_callback_query(call.id, "Validé !")
                bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id, caption=f"{call.message.caption}\n\n✅ **STATUT : APPROUVÉ**")

                send_app_access(target_id, congrats=True)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_reject_"))
    def admin_reject(call):
        if call.from_user.id != ADMIN_ID:
            return

        req_id = int(call.data.replace("adm_reject_", ""))
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM pending_validations WHERE id = ?", (req_id,))
            row = cursor.fetchone()

            if row:
                target_id = row["user_id"]
                cursor.execute("UPDATE pending_validations SET status = 'REJECTED' WHERE id = ?", (req_id,))
                conn.commit()

                bot.answer_callback_query(call.id, "Refusé.")
                bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id, caption=f"{call.message.caption}\n\n❌ **STATUT : REFUSÉ**")

                try:
                    bot.send_message(target_id, "❌ **Demande Rejetée :** Vos preuves n'ont pas pu être vérifiées. Assurez-vous d'utiliser notre lien/code promo et d'effectuer le dépôt.")
                except Exception:
                    pass

    def send_app_access(chat_id, congrats=False):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📈 Ouvrir la Mini App Trading", web_app=telebot.types.WebAppInfo(url=URL_MINI_APP_TRADING)))

        prefix = "🎉 **FÉLICITATIONS ! Votre compte est validé.**\n\n" if congrats else ""
        bot.send_message(
            chat_id,
            f"{prefix}Accédez au terminal complet en cliquant ci-dessous :",
            reply_markup=markup
        )

    # ---------------------------------------------------------
    # 6. PAIEMENT TELEGRAM STARS (MONNAIE XTR)
    # ---------------------------------------------------------
    @bot.callback_query_handler(func=lambda call: call.data.startswith("pay_stars_"))
    def process_stars_payment(call):
        stars_amount = int(call.data.replace("pay_stars_", ""))
        bot.answer_callback_query(call.id)

        prices = [LabeledPrice(label="Pass VIP Terminal Trading", amount=stars_amount)]

        bot.send_invoice(
            call.message.chat.id,
            title="Pass VIP Terminal Trading",
            description="Déblocage de tous les TP, SL et fonctionnalités IA.",
            invoice_payload=f"vip_stars_{stars_amount}",
            provider_token="",
            currency="XTR",
            prices=prices,
            start_parameter="vip-pay"
        )

    @bot.pre_checkout_query_handler(func=lambda query: True)
    def checkout(pre_checkout_query):
        bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

    @bot.message_handler(content_types=['successful_payment'])
    def got_payment(msg):
        user_id = msg.from_user.id
        payload = msg.successful_payment.invoice_payload

        days = 30
        if "1200" in payload:
            days = 90
        elif "3500" in payload:
            days = 365

        expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET status = 'VIP', vip_expiry = ?, funnel_step = 'COMPLETED' WHERE user_id = ?", (expiry, user_id))
            conn.commit()

        send_app_access(msg.chat.id, congrats=True)

# ---------------------------------------------------------
# 7. AUTOMATISATIONS & RELANCES (CRON)
# ---------------------------------------------------------
def send_funnel_reminders():
    """Relances aux utilisateurs bloqués à 6h, 12h et 18h."""
    if not bot: return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE status = 'FREE' AND funnel_step != 'COMPLETED'")
        rows = cursor.fetchall()

        for row in rows:
            user_id = row["user_id"]
            msg = "⏳ **Rappel Trading :** Complétez votre inscription Exness/KuCoin ou débloquez l'accès via Telegram Stars pour utiliser le Terminal IA !"
            try:
                bot.send_message(user_id, msg)
                time.sleep(0.05)
            except Exception:
                pass

def publish_crypto_market_alert():
    """Alerte automatique dans le canal."""
    if not CHANNEL_ID or not bot:
        return
    try:
        res = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true", timeout=10).json()
        btc_p, btc_c = res["bitcoin"]["usd"], res["bitcoin"]["usd_24h_change"]
        eth_p, eth_c = res["ethereum"]["usd"], res["ethereum"]["usd_24h_change"]

        b_i = "🟢" if btc_c >= 0 else "🔴"
        e_i = "🟢" if eth_c >= 0 else "🔴"

        msg = (
            "📊 **ANALYSE DU MARCHÉ CRYPTO**\n\n"
            f"🪙 **BTC/USD :** `{btc_p} $` ({b_i} {btc_c:.2f}%)\n"
            f"🔹 **ETH/USD :** `{eth_p} $` ({e_i} {eth_c:.2f}%)\n\n"
            "🔥 *Détectez les opportunités d'achat/vente sur la Mini App !"
        )
        bot.send_message(CHANNEL_ID, msg)
    except Exception as e:
        logger.error(f"Erreur alerte crypto : {e}")

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(send_funnel_reminders, 'cron', hour='6,12,18')
scheduler.add_job(publish_crypto_market_alert, 'interval', hours=6)
scheduler.start()

# ---------------------------------------------------------
# 8. API FLASK DE VERIFICATION POUR LA MINI APP WEB
# ---------------------------------------------------------
app = Flask(__name__)

@app.route("/api/user-status", methods=["POST"])
def get_user_status():
    data = request.json or {}
    user_id = data.get("userId")
    if not user_id:
        return jsonify({"error": "userId manquant"}), 400

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, vip_expiry FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()

        if not row:
            return jsonify({"status": "UNREGISTERED", "isVip": False, "isAdmin": (int(user_id) == ADMIN_ID)})

        is_vip = (row["status"] == "VIP" or int(user_id) == ADMIN_ID)
        return jsonify({
            "status": row["status"],
            "isVip": is_vip,
            "isAdmin": (int(user_id) == ADMIN_ID)
        })

def run_flask():
    app.run(host="127.0.0.1", port=5000)

if __name__ == "__main__":
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    logger.info("⚡ API Flask interne démarrée sur le port 5000 !")
    if bot:
        logger.info("🤖 Bot Telegram Démarré !")
        bot.infinity_polling(none_stop=True, skip_pending=True)