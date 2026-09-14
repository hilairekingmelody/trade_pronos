import os
import sys
import logging
import sqlite3
from datetime import datetime, timedelta
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TradingBot")

# Variables récupérées depuis l'environnement Render
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.environ.get("ADMIN_ID", "0").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0

# Liens intégrés directement dans le code
CHANNEL_LINK = "https://t.me/trading_pronos"
CHANNEL_ID = "@trading_pronos"
URL_MINI_APP_TRADING = "https://trading-3wcr.onrender.com"

if not TOKEN:
    raise ValueError("❌ Variable TELEGRAM_BOT_TOKEN manquante dans l'environnement Render.")

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")

user_states = {}
user_temp_data = {}

# Affiliations Trading
AFFILIATES = {
    "exness": {
        "name": "Exness (Forex)",
        "link": "https://one.exnessonelink.com/a/395vyusacl",
        "promo": "395vyusacl"
    },
    "kucoin": {
        "name": "KuCoin (Crypto)",
        "link": "https://www.kucoin.com/ucenter/signup?&rcode=rEN8V1E&utm_medium=U17710",
        "promo": "rEN8V1E"
    }
}

DB_FILE = "bot_trading.db"

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                status TEXT DEFAULT 'FREE',
                vip_expiry TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS validations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                account_id TEXT,
                photo_id TEXT,
                status TEXT DEFAULT 'PENDING'
            )
        """)
        conn.commit()

init_db()

def is_channel_member(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["creator", "administrator", "member"]
    except Exception as e:
        logger.error(f"Erreur vérification canal: {e}")
        # En cas d'erreur de vérification par API, autoriser par défaut
        return True

@bot.message_handler(commands=["start"])
def start_command(msg):
    user_id = msg.from_user.id
    username = msg.from_user.username or "Inconnu"

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            cursor.execute("INSERT INTO users (user_id, username, status) VALUES (?, ?, 'FREE')", (user_id, username))
            conn.commit()
            status = "FREE"
        else:
            status = row["status"]

    # 1. Vérification du Canal Telegram
    if not is_channel_member(user_id):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📢 Rejoindre le Canal", url=CHANNEL_LINK))
        markup.add(InlineKeyboardButton("✅ J'ai rejoint", callback_data="check_join"))
        bot.reply_to(msg, "🔒 **ACCÈS RESTREINT**\n\nVeuillez d'abord rejoindre notre canal officiel pour continuer.", reply_markup=markup)
        return

    # 2. Si l'utilisateur est déjà validé / VIP ou Admin
    if status in ["VIP", "PREMIUM"] or user_id == ADMIN_ID:
        send_app_button(msg.chat.id)
        return

    # 3. Étape d'inscription via lien affilié
    send_affiliation_step(msg.chat.id)

@bot.callback_query_handler(func=lambda call: call.data == "check_join")
def callback_check_join(call):
    if is_channel_member(call.from_user.id):
        bot.answer_callback_query(call.id, "✅ Merci d'avoir rejoint !")
        bot.delete_message(call.message.chat.id, call.message.message_id)
        send_affiliation_step(call.message.chat.id)
    else:
        bot.answer_callback_query(call.id, "❌ Vous n'avez pas encore rejoint le canal !", show_alert=True)

def send_affiliation_step(chat_id):
    text = (
        "📈 **ACCÈS AU TERMINAL TRADING**\n\n"
        "Pour activer l'accès à la Mini App Trading, créez un compte sur l'un des brokers partenaires et déposez au moins 10$ :\n\n"
        f"🔹 **Exness** (Forex) — Code Promo : `{AFFILIATES['exness']['promo']}`\n"
        f"👉 [Lien d'inscription Exness]({AFFILIATES['exness']['link']})\n\n"
        f"🔹 **KuCoin** (Crypto) — Code Promo : `{AFFILIATES['kucoin']['promo']}`\n"
        f"👉 [Lien d'inscription KuCoin]({AFFILIATES['kucoin']['link']})\n\n"
        "Envoyez vos justificatifs après votre dépôt."
    )
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📤 Envoyer mes preuves (ID + Dépôt)", callback_data="submit_proof"))
    bot.send_message(chat_id, text, reply_markup=markup, disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda call: call.data == "submit_proof")
def start_proof_submission(call):
    chat_id = call.message.chat.id
    user_states[chat_id] = "WAITING_ID"
    bot.answer_callback_query(call.id)
    bot.send_message(chat_id, "📝 **Étape 1/2 :** Entrez votre **ID de compte** (Exness ou KuCoin) :")

@bot.message_handler(func=lambda msg: user_states.get(msg.chat.id) == "WAITING_ID")
def process_account_id(msg):
    chat_id = msg.chat.id
    user_temp_data[chat_id] = {"account_id": msg.text.strip()}
    user_states[chat_id] = "WAITING_PHOTO"
    bot.reply_to(msg, "📸 **Étape 2/2 :** Envoyez maintenant la **capture d'écran** de votre preuve de dépôt.")

@bot.message_handler(content_types=["photo"], func=lambda msg: user_states.get(msg.chat.id) == "WAITING_PHOTO")
def process_proof_photo(msg):
    chat_id = msg.chat.id
    user_id = msg.from_user.id
    photo_id = msg.photo[-1].file_id
    account_id = user_temp_data.get(chat_id, {}).get("account_id", "Inconnu")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO validations (user_id, account_id, photo_id) VALUES (?, ?, ?)",
            (user_id, account_id, photo_id)
        )
        req_id = cursor.lastrowid
        conn.commit()

    user_states[chat_id] = None
    user_temp_data[chat_id] = None

    bot.reply_to(msg, "✅ **Preuves enregistrées !** Elles ont été transmises à l'administrateur pour vérification.")

    if ADMIN_ID != 0:
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("✅ Valider & Débloquer", callback_data=f"adm_accept_{req_id}"),
            InlineKeyboardButton("❌ Refuser", callback_data=f"adm_reject_{req_id}")
        )
        caption = (
            f"🔔 **DEMANDE D'ACCÈS TRADING**\n\n"
            f"👤 User ID : `{user_id}` (@{msg.from_user.username or 'N/A'})\n"
            f"🆔 Account ID : `{account_id}`"
        )
        bot.send_photo(ADMIN_ID, photo_id, caption=caption, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_accept_"))
def admin_accept(call):
    if call.from_user.id != ADMIN_ID:
        return

    req_id = int(call.data.replace("adm_accept_", ""))
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM validations WHERE id = ?", (req_id,))
        row = cursor.fetchone()

        if row:
            target_id = row["user_id"]
            cursor.execute("UPDATE users SET status = 'VIP' WHERE user_id = ?", (target_id,))
            cursor.execute("UPDATE validations SET status = 'APPROVED' WHERE id = ?", (req_id,))
            conn.commit()

            bot.answer_callback_query(call.id, "Utilisateur validé !")
            bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id, caption=f"{call.message.caption}\n\n✅ **STATUT : VALIDE**")

            send_app_button(target_id, congrats=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_reject_"))
def admin_reject(call):
    if call.from_user.id != ADMIN_ID:
        return

    req_id = int(call.data.replace("adm_reject_", ""))
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM validations WHERE id = ?", (req_id,))
        row = cursor.fetchone()

        if row:
            target_id = row["user_id"]
            cursor.execute("UPDATE validations SET status = 'REJECTED' WHERE id = ?", (req_id,))
            conn.commit()

            bot.answer_callback_query(call.id, "Demande refusée.")
            bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id, caption=f"{call.message.caption}\n\n❌ **STATUT : REFUSÉ**")
            bot.send_message(target_id, "❌ **Votre demande a été refusée.** Veuillez vous assurer d'avoir bien suivi les instructions d'inscription et de dépôt.")

def send_app_button(chat_id, congrats=False):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🚀 Ouvrir le Terminal Trading", web_app=WebAppInfo(url=URL_MINI_APP_TRADING)))

    prefix = "🎉 **Félicitations, votre accès a été validé !**\n\n" if congrats else ""
    bot.send_message(chat_id, f"{prefix}Accédez dès maintenant à votre Mini App Trading ci-dessous :", reply_markup=markup)

if __name__ == "__main__":
    bot.infinity_polling(none_stop=True)