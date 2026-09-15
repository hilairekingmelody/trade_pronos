import os
import sys
import time
import logging
import sqlite3
import json
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
from flask_cors import CORS
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

TOKEN = (
    os.environ.get("BOT_TOKEN", "").strip()
    or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
)
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "@trading_pronos").strip()

ADMIN_ID_RAW = os.environ.get("ADMIN_ID", "0").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown") if TOKEN else None

user_states = {}
user_temp_data = {}

URL_MINI_APP_TRADING = os.environ.get(
    "URL_MINI_APP_TRADING", "https://trading-3wcr.onrender.com"
)

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
                last_active TEXT,
                linked_account TEXT DEFAULT NULL,
                last_reminder_sent TEXT DEFAULT NULL
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
# 3. VERIFICATION CANAL (FORCE JOIN)
# ---------------------------------------------------------
def check_channel_membership(user_id: int) -> bool:
    if not CHANNEL_ID or not bot or user_id == ADMIN_ID:
        return True
    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["creator", "administrator", "member"]
    except Exception as e:
        logger.error(f"Erreur contrôle canal : {e}")
        return False

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
        referrer_id = (
            int(args[1])
            if len(args) > 1 and args[1].isdigit() and int(args[1]) != user_id
            else None
        )

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, status FROM users WHERE user_id = ?", (user_id,)
            )
            row = cursor.fetchone()

            if not row:
                cursor.execute(
                    "INSERT INTO users (user_id, username, status, funnel_step, referrer_id, last_active) VALUES (?, ?, 'FREE', 'STARTED', ?, ?)",
                    (user_id, username, referrer_id, now_str),
                )
                if referrer_id:
                    cursor.execute(
                        "UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id = ?",
                        (referrer_id,),
                    )
                conn.commit()
                status = "FREE"
            else:
                status = row["status"]
                cursor.execute(
                    "UPDATE users SET last_active = ? WHERE user_id = ?",
                    (now_str, user_id),
                )
                conn.commit()

        # OBLIGATION DE REJOINDRE LE CANAL
        if not check_channel_membership(user_id):
            markup = InlineKeyboardMarkup()
            clean_channel = CHANNEL_ID.replace("@", "")
            markup.add(
                InlineKeyboardButton(
                    "📢 Rejoindre le Canal Officiel", url=f"https://t.me/{clean_channel}"
                )
            )
            markup.add(
                InlineKeyboardButton(
                    "✅ J'ai rejoint le canal", callback_data="check_join"
                )
            )
            bot.reply_to(
                msg,
                "🔒 **ACCÈS RESTREINT**\n\nVous devez obligatoirement rejoindre notre canal officiel pour pouvoir accéder au bot et aux signaux.",
                reply_markup=markup,
            )
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
            bot.answer_callback_query(
                call.id, "❌ Vous n'avez pas encore rejoint le canal !", show_alert=True
            )

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
        markup.add(
            InlineKeyboardButton(
                "📤 Envoyer mes preuves de dépôt", callback_data="submit_proof"
            )
        )
        markup.add(
            InlineKeyboardButton(
                "⭐ Pass VIP (1 Mois) - 500 Stars", callback_data="pay_stars_500"
            )
        )
        bot.send_message(
            chat_id, text, reply_markup=markup, disable_web_page_preview=True
        )

    # ---------------------------------------------------------
    # PROOF SUBMISSION & ADMIN APPROVAL
    # ---------------------------------------------------------
    @bot.callback_query_handler(func=lambda call: call.data == "submit_proof")
    def start_proof_submission(call):
        chat_id = call.message.chat.id
        user_states[chat_id] = "WAITING_ID"
        user_temp_data[chat_id] = {}
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "📝 **Étape 1/2 :** Entrez votre **ID de compte** Exness ou KuCoin :")

    @bot.message_handler(func=lambda msg: user_states.get(msg.chat.id) == "WAITING_ID")
    def process_account_id(msg):
        chat_id = msg.chat.id
        user_temp_data[chat_id]["account_id"] = msg.text.strip()
        user_states[chat_id] = "WAITING_PHOTO"
        bot.reply_to(msg, "📸 **Étape 2/2 :** Envoyez la **capture d'écran** de votre dépôt de 10$.")

    @bot.message_handler(content_types=["photo"], func=lambda msg: user_states.get(msg.chat.id) == "WAITING_PHOTO")
    def process_proof_photo(msg):
        chat_id = msg.chat.id
        user_id = msg.from_user.id
        photo_id = msg.photo[-1].file_id
        account_id = user_temp_data.get(chat_id, {}).get("account_id", "Inconnu")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO pending_validations (user_id, universe, account_id, photo_id, created_at) VALUES (?, 'trading', ?, ?, ?)",
                (user_id, account_id, photo_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            cursor.execute("UPDATE users SET funnel_step = 'PROOFS_SENT' WHERE user_id = ?", (user_id,))
            req_id = cursor.lastrowid
            conn.commit()

        user_states[chat_id] = None
        bot.reply_to(msg, "✅ **Preuves transmises !** Vérification en cours par l'administrateur.")

        if ADMIN_ID != 0:
            markup = InlineKeyboardMarkup()
            markup.add(
                InlineKeyboardButton("✅ Valider (VIP 30J)", callback_data=f"adm_accept_{req_id}"),
                InlineKeyboardButton("❌ Rejeter", callback_data=f"adm_reject_{req_id}")
            )
            admin_text = f"🔔 **DEMANDE VIP**\n👤 User: `{user_id}`\n🆔 Compte: `{account_id}`"
            bot.send_photo(ADMIN_ID, photo_id, caption=admin_text, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_accept_"))
    def admin_accept(call):
        if call.from_user.id != ADMIN_ID:
            return
        req_id = int(call.data.replace("adm_accept_", ""))
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, account_id FROM pending_validations WHERE id = ?", (req_id,))
            row = cursor.fetchone()
            if row:
                target_id = row["user_id"]
                account_id = row["account_id"]
                expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

                cursor.execute(
                    "UPDATE users SET status = 'VIP', vip_expiry = ?, linked_account = ?, funnel_step = 'COMPLETED' WHERE user_id = ?",
                    (expiry, account_id, target_id),
                )
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
                    bot.send_message(target_id, "❌ **Demande Rejetée :** Preuves invalides.")
                except Exception:
                    pass

    def send_app_access(chat_id, congrats=False):
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(
                "📈 Ouvrir la Mini App Trading",
                web_app=telebot.types.WebAppInfo(url=URL_MINI_APP_TRADING),
            )
        )
        prefix = "🎉 **FÉLICITATIONS ! Votre compte est validé.**\n\n" if congrats else ""
        bot.send_message(chat_id, f"{prefix}Accédez au terminal complet ci-dessous :", reply_markup=markup)

    # ---------------------------------------------------------
    # TELEGRAM STARS INVOICING
    # ---------------------------------------------------------
    @bot.callback_query_handler(func=lambda call: call.data.startswith("pay_stars_"))
    def process_stars_payment(call):
        stars_amount = int(call.data.replace("pay_stars_", ""))
        bot.answer_callback_query(call.id)
        prices = [LabeledPrice(label="Pass VIP Terminal Trading (1 Mois)", amount=stars_amount)]

        bot.send_invoice(
            call.message.chat.id,
            title="Pass VIP Terminal Trading (1 Mois)",
            description="Déblocage complet des TP1, TP2, Stop Loss et signaux IA pour 30 jours.",
            invoice_payload="vip_stars_500",
            provider_token="",
            currency="XTR",
            prices=prices,
            start_parameter="vip-pay",
        )

    @bot.pre_checkout_query_handler(func=lambda query: True)
    def checkout(pre_checkout_query):
        bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

    @bot.message_handler(content_types=["successful_payment"])
    def got_payment(msg):
        user_id = msg.from_user.id
        expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET status = 'VIP', vip_expiry = ?, funnel_step = 'COMPLETED' WHERE user_id = ?",
                (expiry, user_id),
            )
            conn.commit()

        send_app_access(msg.chat.id, congrats=True)

# ---------------------------------------------------------
# 5. TÂCHES AUTOMATIQUES (RELANCES 3H & ALERTES MARCHÉ)
# ---------------------------------------------------------
def send_3h_reminders():
    """Envoie une relance aux utilisateurs bloqués depuis plus de 3h."""
    if not bot:
        return
    now = datetime.now()
    three_hours_ago = (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id FROM users 
            WHERE status = 'FREE' 
              AND funnel_step = 'STARTED' 
              AND last_active <= ? 
              AND last_reminder_sent IS NULL
        """, (three_hours_ago,))
        pending_users = cursor.fetchall()

        for user in pending_users:
            u_id = user["user_id"]
            try:
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("📤 Finaliser l'inscription VIP", callback_data="submit_proof"))
                bot.send_message(
                    u_id,
                    "⏰ **N'OUBLIEZ PAS VOTRE ACCÈS VIP !**\n\n"
                    "Il vous suffit d'effectuer un dépôt de **10 $** chez l'un de nos partenaires pour débloquer **100% des signaux** et des TP/SL sur le terminal.\n\n"
                    "Cliquez ci-dessous pour nous envoyer vos preuves :",
                    reply_markup=markup
                )
                cursor.execute("UPDATE users SET last_reminder_sent = ? WHERE user_id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), u_id))
            except Exception as e:
                logger.error(f"Échec relance pour {u_id}: {e}")
        conn.commit()

def publish_market_alerts():
    """Récupère les prix de BTC, ETH et EUR/USD et les publie dans le canal."""
    if not bot or not CHANNEL_ID:
        return
    try:
        # Récupération Crypto via CoinGecko
        cg_res = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true", timeout=10).json()
        btc_price = cg_res.get("bitcoin", {}).get("usd", 0)
        btc_change = cg_res.get("bitcoin", {}).get("usd_24h_change", 0)
        eth_price = cg_res.get("ethereum", {}).get("usd", 0)
        eth_change = cg_res.get("ethereum", {}).get("usd_24h_change", 0)

        # Récupération EUR/USD via Frankfurter API
        fx_res = requests.get("https://api.frankfurter.app/latest?from=EUR&to=USD", timeout=10).json()
        eur_usd = fx_res.get("rates", {}).get("USD", 0)

        btc_icon = "🟢" if btc_change >= 0 else "🔴"
        eth_icon = "🟢" if eth_change >= 0 else "🔴"

        msg = (
            "📊 **UPDATE MARCHÉ EN TEMPS RÉEL**\n\n"
            f"🪙 **Bitcoin (BTC) :** `{btc_price:,.2f} $` ({btc_icon} {btc_change:+.2f}%)\n"
            f"💎 **Ethereum (ETH) :** `{eth_price:,.2f} $` ({eth_icon} {eth_change:+.2f}%)\n"
            f"💱 **EUR / USD :** `{eur_usd:.4f}`\n\n"
            "📈 *Consultez votre Mini App pour voir les signaux IA associés !*"
        )
        bot.send_message(CHANNEL_ID, msg)
    except Exception as e:
        logger.error(f"Erreur publication alerte marché: {e}")

# Lancement du planificateur en arrière-plan
scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(send_3h_reminders, 'interval', minutes=15) # Vérifie toutes les 15 min
scheduler.add_job(publish_market_alerts, 'interval', hours=1) # Alerte marché toutes les heures
scheduler.start()

# ---------------------------------------------------------
# 6. API FLASK & ROUTE WEBHOOK / HEALTH
# ---------------------------------------------------------
app = Flask(__name__)
CORS(app)

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "online", "message": "Bot & API Opérationnels"}), 200

@app.route("/api/create-stars-invoice", methods=["POST"])
def create_stars_invoice():
    data = request.json or {}
    user_id = data.get("userId")
    stars = data.get("stars", 500)

    if not user_id or not bot:
        return jsonify({"error": "Paramètres manquants ou Bot inactif"}), 400

    try:
        prices = [LabeledPrice(label="Pass VIP Terminal Trading (1 Mois)", amount=int(stars))]
        invoice_link = bot.create_invoice_link(
            title="Pass VIP Terminal Trading (1 Mois)",
            description="Déblocage complet des TP1, TP2, Stop Loss et signaux IA pour 30 jours.",
            payload="vip_stars_500",
            provider_token="",
            currency="XTR",
            prices=prices
        )
        return jsonify({"success": True, "invoiceUrl": invoice_link})
    except Exception as e:
        logger.error(f"Erreur création facture Stars: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/user-status", methods=["POST"])
def get_user_status():
    data = request.json or {}
    user_id = data.get("userId")
    if not user_id:
        return jsonify({"error": "userId manquant"}), 400

    user_id_int = int(user_id)
    now = datetime.now()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, vip_expiry, referrals_count, linked_account FROM users WHERE user_id = ?", (user_id_int,)
        )
        row = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) as total FROM users")
        total_users = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) as vip FROM users WHERE status = 'VIP'")
        vip_users = cursor.fetchone()["vip"]

        if not row:
            return jsonify({
                "status": "UNREGISTERED",
                "isVip": (user_id_int == ADMIN_ID),
                "isAdmin": (user_id_int == ADMIN_ID),
                "isLinked": False,
                "linkedAccount": None,
                "referralsCount": 0,
                "stats": {"totalUsers": total_users, "vipUsers": vip_users}
            })

        is_vip = False
        if row["status"] == "VIP":
            if row["vip_expiry"]:
                try:
                    exp_date = datetime.strptime(row["vip_expiry"], "%Y-%m-%d %H:%M:%S")
                    if exp_date > now:
                        is_vip = True
                    else:
                        cursor.execute("UPDATE users SET status = 'FREE' WHERE user_id = ?", (user_id_int,))
                        conn.commit()
                except Exception:
                    is_vip = True
            else:
                is_vip = True

        if user_id_int == ADMIN_ID:
            is_vip = True

        linked_acc = row["linked_account"]
        is_linked = True if (linked_acc or is_vip) else False

        return jsonify({
            "status": "VIP" if is_vip else "FREE",
            "isVip": is_vip,
            "isAdmin": (user_id_int == ADMIN_ID),
            "isLinked": is_linked,
            "linkedAccount": linked_acc,
            "referralsCount": row["referrals_count"] or 0,
            "stats": {"totalUsers": total_users, "vipUsers": vip_users}
        })

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    logger.info("⚡ API Flask Web Service démarrée !")
    if bot:
        logger.info("🤖 Bot Telegram Démarré !")
        bot.infinity_polling(none_stop=True, skip_pending=True)