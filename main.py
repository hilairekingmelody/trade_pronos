import os
import sys
import time
import logging
import sqlite3
from datetime import datetime, timedelta
from threading import Thread

from flask import Flask
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

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
FOOTBALL_KEY = os.environ.get("FOOTBALL_API_KEY", "").strip() or os.environ.get("API_FOOTBALL", "").strip()
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()

ADMIN_ID_RAW = os.environ.get("ADMIN_ID", "0").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0

PAYMENT_PROVIDER_TOKEN = os.environ.get("PAYMENT_PROVIDER_TOKEN", "").strip()

if not TOKEN:
    raise ValueError("❌ ERREUR CRITIQUE: La variable TELEGRAM_BOT_TOKEN est introuvable !")

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")

user_states = {}
user_temp_data = {}

# ---------------------------------------------------------
# 2. CATALOGUE DES AFFILIATIONS ET MINI APPS SEPARÉES
# ---------------------------------------------------------
AFFILIATES_TRADING = {
    "exness": {
        "name": "Exness — Forex & Trading",
        "link": "https://one.exnessonelink.com/a/395vyusacl",
        "promo": "395vyusacl",
        "min_dep": "10 $",
    },
    "kucoin": {
        "name": "KuCoin — Crypto Exchange",
        "link": "https://www.kucoin.com/ucenter/signup?&rcode=rEN8V1E&utm_medium=U17710",
        "promo": "rEN8V1E",
        "min_dep": "10 $",
    },
}

AFFILIATES_BETTING = {
    "1xbet": {
        "name": "1xBet — Paris Sportifs",
        "link": "https://reffpa.com/L?tag=d_5087549m_1573c_whatsapp&site=5087549&ad=1573",
        "promo": "HILAIREBET",
        "min_dep": "5 $",
    },
    "melbet": {
        "name": "MelBet — Paris Sportifs",
        "link": "https://refpa3665.com/L?tag=d_5997062m_53523c_whatsapp&site=5997062&ad=53523",
        "promo": "HILAIREBET",
        "min_dep": "5 $",
    },
}

URL_MINI_APP_TRADING = os.environ.get("URL_MINI_APP_TRADING", "https://t.me/ton_bot/trading_app")
URL_MINI_APP_BETTING = os.environ.get("URL_MINI_APP_BETTING", "https://t.me/ton_bot/betting_app")

# ---------------------------------------------------------
# 3. BASE DE DONNÉES SQLITE
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
                chosen_universe TEXT,
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
# 4. VERIFICATION FORCE JOIN (CANAL)
# ---------------------------------------------------------
def check_channel_membership(user_id: int) -> bool:
    if not CHANNEL_ID or user_id == ADMIN_ID:
        return True
    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["creator", "administrator", "member"]
    except Exception as e:
        logger.error(f"Erreur contrôle canal : {e}")
        return True

# ---------------------------------------------------------
# 5. AUTOMATISATIONS DANS LE CANAL (CRYPTO & FOOT)
# ---------------------------------------------------------
def publish_crypto_update():
    """Publie la variation des prix de BTC et ETH directement dans le canal."""
    if not CHANNEL_ID:
        return
    try:
        res = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true",
            timeout=10
        ).json()
        btc_price = res["bitcoin"]["usd"]
        btc_change = res["bitcoin"]["usd_24h_change"]
        eth_price = res["ethereum"]["usd"]
        eth_change = res["ethereum"]["usd_24h_change"]

        btc_icon = "🟢" if btc_change >= 0 else "🔴"
        eth_icon = "🟢" if eth_change >= 0 else "🔴"

        msg = (
            "📊 **ALERTE MARCHÉ CRYPTO (CANAL)**\n\n"
            f"🪙 **Bitcoin (BTC) :** `{btc_price} $` | 24h : {btc_icon} `{btc_change:.2f}%`\n"
            f"🔷 **Ethereum (ETH) :** `{eth_price} $` | 24h : {eth_icon} `{eth_change:.2f}%`\n\n"
            "📈 *Retrouvez les signaux complets sur la Mini App Trading !*"
        )
        bot.send_message(CHANNEL_ID, msg)
    except Exception as e:
        logger.error(f"Erreur alerte crypto : {e}")

def publish_daily_matches():
    """Publie les matchs à l'affiche du jour directement dans le canal."""
    if not CHANNEL_ID or not FOOTBALL_KEY:
        return
    try:
        headers = {"X-Auth-Token": FOOTBALL_KEY}
        today = datetime.now().strftime("%Y-%m-%d")
        url = f"https://api.football-data.org/v4/matches?dateFrom={today}&dateTo={today}"
        res = requests.get(url, headers=headers, timeout=10).json()
        matches = res.get("matches", [])

        if not matches:
            return

        text = "⚽ **MATCHS À L'AFFICHE DU JOUR**\n\n"
        for match in matches[:8]:
            home = match["homeTeam"]["name"]
            away = match["awayTeam"]["name"]
            league = match["competition"]["name"]
            text += f"🏆 *{league}* : {home} vs {away}\n"

        text += "\n🔥 *Consultez nos prédictions IA sur la Mini App Pronostics !*"
        bot.send_message(CHANNEL_ID, text)
    except Exception as e:
        logger.error(f"Erreur alerte football : {e}")

# ---------------------------------------------------------
# 6. PARCOURS UTILISATEUR ET SÉLECTION D'UNIVERS
# ---------------------------------------------------------
@bot.message_handler(commands=["start"])
def start_cmd(msg):
    user_id = msg.from_user.id
    username = msg.from_user.username or "Inconnu"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    args = msg.text.split()
    referrer_id = int(args[1]) if len(args) > 1 and args[1].isdigit() and int(args[1]) != user_id else None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, chosen_universe, status FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()

        if not row:
            cursor.execute(
                "INSERT INTO users (user_id, username, status, funnel_step, referrer_id, last_active) VALUES (?, ?, 'FREE', 'STARTED', ?, ?)",
                (user_id, username, referrer_id, now_str)
            )
            if referrer_id:
                cursor.execute("UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id = ?", (referrer_id,))
                # Incrémentation parrainage : 3 filleuls = 1 Mois VIP
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
            chosen_universe = None
            status = "FREE"
        else:
            chosen_universe = row["chosen_universe"]
            status = row["status"]
            cursor.execute("UPDATE users SET last_active = ? WHERE user_id = ?", (now_str, user_id))
            conn.commit()

    # ÉTAPE 1 : CANAL OBLIGATOIRE
    if not check_channel_membership(user_id):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📢 Rejoindre le Canal", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}"))
        markup.add(InlineKeyboardButton("✅ J'ai rejoint le canal", callback_data="check_join"))
        bot.reply_to(msg, "🔒 **ACCÈS RESTREINT**\n\nVous devez obligatoirement rejoindre notre canal officiel pour utiliser le bot.", reply_markup=markup)
        return

    # Si l'utilisateur est déjà VIP, ouvrir directement son univers
    if status in ["VIP", "PREMIUM"] and chosen_universe:
        send_app_access(msg.chat.id, chosen_universe)
        return

    # ÉTAPE 2 : DEMANDER L'INTÉRÊT (TRADING OU PARIS SPORTIFS)
    send_universe_selection(msg.chat.id)

@bot.callback_query_handler(func=lambda call: call.data == "check_join")
def callback_check_join(call):
    if check_channel_membership(call.from_user.id):
        bot.answer_callback_query(call.id, "✅ Accès validé !")
        bot.delete_message(call.message.chat.id, call.message.message_id)
        send_universe_selection(call.message.chat.id)
    else:
        bot.answer_callback_query(call.id, "❌ Vous n'avez pas encore rejoint le canal !", show_alert=True)

def send_universe_selection(chat_id):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📊 Trading (Forex & Crypto)", callback_data="select_universe_trading"))
    markup.add(InlineKeyboardButton("⚽ Paris Sportifs (Pronostics)", callback_data="select_universe_betting"))

    bot.send_message(
        chat_id,
        "👋 **BIENVENUE !**\n\n"
        "Lequel de nos services vous intéresse le plus aujourd'hui ?",
        reply_markup=markup
    )

# ---------------------------------------------------------
# 7. BRANCHES ÉTANCHES : TRADING VS BETTING
# ---------------------------------------------------------
@bot.callback_query_handler(func=lambda call: call.data.startswith("select_universe_"))
def handle_universe_choice(call):
    universe = call.data.replace("select_universe_", "")
    user_id = call.from_user.id

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET chosen_universe = ?, funnel_step = 'UNIVERSE_SELECTED' WHERE user_id = ?", (universe, user_id))
        conn.commit()

    bot.answer_callback_query(call.id)

    if universe == "trading":
        send_trading_funnel(call.message.chat.id)
    else:
        send_betting_funnel(call.message.chat.id)

def send_trading_funnel(chat_id):
    text = (
        "📊 **ACCÈS AUX SIGNAUX DE TRADING**\n\n"
        "Pour débloquer votre accès VIP à la Mini App Trading, inscrivez-vous chez l'un de nos partenaires et effectuez un dépôt minimum de **10 $** :\n\n"
        "🔹 **Exness** (Forex) — Code Promo : `395vyusacl`\n"
        "👉 [S'inscrire sur Exness](https://one.exnessonelink.com/a/395vyusacl)\n\n"
        "🔹 **KuCoin** (Crypto) — Code Promo : `rEN8V1E`\n"
        "👉 [S'inscrire sur KuCoin](https://www.kucoin.com/ucenter/signup?&rcode=rEN8V1E&utm_medium=U17710)\n\n"
        "👇 *Une fois inscrit et votre dépôt de 10$ effectué, cliquez ci-dessous :*"
    )
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📤 Envoyer mes preuves de dépôt (Trading)", callback_data="submit_proof_trading"))
    markup.add(InlineKeyboardButton("⭐ Payer via Telegram Stars", callback_data="pay_stars"))
    bot.send_message(chat_id, text, reply_markup=markup, disable_web_page_preview=True)

def send_betting_funnel(chat_id):
    text = (
        "⚽ **ACCÈS AUX PRONOSTICS SPORTIFS**\n\n"
        "Pour débloquer votre accès VIP à la Mini App Pronostics, inscrivez-vous chez l'un de nos partenaires et effectuez un dépôt minimum de **5 $** :\n\n"
        "🔴 **1xBet** — Code Promo : `HILAIREBET`\n"
        "👉 [S'inscrire sur 1xBet](https://reffpa.com/L?tag=d_5087549m_1573c_whatsapp&site=5087549&ad=1573)\n\n"
        "🔵 **MelBet** — Code Promo : `HILAIREBET`\n"
        "👉 [S'inscrire sur MelBet](https://refpa3665.com/L?tag=d_5997062m_53523c_whatsapp&site=5997062&ad=53523)\n\n"
        "👇 *Une fois inscrit et votre dépôt de 5$ effectué, cliquez ci-dessous :*"
    )
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📤 Envoyer mes preuves de dépôt (Pronos)", callback_data="submit_proof_betting"))
    markup.add(InlineKeyboardButton("⭐ Payer via Telegram Stars", callback_data="pay_stars"))
    bot.send_message(chat_id, text, reply_markup=markup, disable_web_page_preview=True)

# ---------------------------------------------------------
# 8. SOUMISSION & VALIDATION PAR L'ADMIN
# ---------------------------------------------------------
@bot.callback_query_handler(func=lambda call: call.data.startswith("submit_proof_"))
def start_proof_submission(call):
    universe = call.data.replace("submit_proof_", "")
    chat_id = call.message.chat.id

    user_states[chat_id] = "WAITING_ID"
    user_temp_data[chat_id] = {"universe": universe}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET funnel_step = 'SUBMITTING_ID' WHERE user_id = ?", (call.from_user.id,))
        conn.commit()

    bot.answer_callback_query(call.id)
    bot.send_message(chat_id, "📝 **Étape 1/2 :** Entrez votre **ID de compte** créé chez le partenaire :")

@bot.message_handler(func=lambda msg: user_states.get(msg.chat.id) == "WAITING_ID")
def process_account_id(msg):
    chat_id = msg.chat.id
    user_temp_data[chat_id]["account_id"] = msg.text.strip()
    user_states[chat_id] = "WAITING_PHOTO"

    bot.reply_to(msg, "📸 **Étape 2/2 :** Envoyez maintenant la **capture d'écran** confirmant votre dépôt.")

@bot.message_handler(content_types=["photo"], func=lambda msg: user_states.get(msg.chat.id) == "WAITING_PHOTO")
def process_proof_photo(msg):
    chat_id = msg.chat.id
    user_id = msg.from_user.id
    photo_id = msg.photo[-1].file_id

    data = user_temp_data.get(chat_id, {})
    universe = data.get("universe", "trading")
    account_id = data.get("account_id", "Inconnu")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO pending_validations (user_id, universe, account_id, photo_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, universe, account_id, photo_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        req_id = cursor.lastrowid
        cursor.execute("UPDATE users SET funnel_step = 'PROOF_SUBMITTED' WHERE user_id = ?", (user_id,))
        conn.commit()

    user_states[chat_id] = None
    user_temp_data[chat_id] = None

    bot.reply_to(msg, "✅ **Preuves reçues !** Vos données ont été transmises à l'administrateur pour vérification.")

    if ADMIN_ID != 0:
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("✅ Valider VIP (30J)", callback_data=f"adm_accept_{req_id}"),
            InlineKeyboardButton("❌ Refuser", callback_data=f"adm_reject_{req_id}")
        )
        admin_text = (
            f"🔔 **NOUVELLE DEMANDE VIP [{universe.upper()}]**\n\n"
            f"👤 **Utilisateur :** `{user_id}` (@{msg.from_user.username or 'Sans pseudo'})\n"
            f"🆔 **ID Compte :** `{account_id}`\n"
            f"🎯 **Service :** `{universe.upper()}`"
        )
        bot.send_photo(ADMIN_ID, photo_id, caption=admin_text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_accept_"))
def admin_accept(call):
    if call.from_user.id != ADMIN_ID:
        return

    req_id = int(call.data.replace("adm_accept_", ""))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, universe FROM pending_validations WHERE id = ?", (req_id,))
        row = cursor.fetchone()

        if row:
            target_id = row["user_id"]
            universe = row["universe"]
            expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

            cursor.execute("UPDATE users SET status = 'VIP', vip_expiry = ?, funnel_step = 'COMPLETED' WHERE user_id = ?", (expiry, target_id))
            cursor.execute("UPDATE pending_validations SET status = 'APPROVED' WHERE id = ?", (req_id,))
            conn.commit()

            bot.answer_callback_query(call.id, "Validé !")
            bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id, caption=f"{call.message.caption}\n\n✅ **STATUT : APPROUVÉ**")

            send_app_access(target_id, universe, congrats=True)

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
                bot.send_message(target_id, "❌ **Accès non accepté** : vos preuves de dépôt n'ont pas été validées. Veuillez vous réinscrire correctement.")
            except Exception:
                pass

def send_app_access(chat_id, universe, congrats=False):
    app_url = URL_MINI_APP_TRADING if universe == "trading" else URL_MINI_APP_BETTING
    label = "📈 Ouvrir la Mini App Trading" if universe == "trading" else "⚽ Ouvrir la Mini App Pronostics"

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(label, web_app=telebot.types.WebAppInfo(url=app_url)))

    prefix = "🎉 **FÉLICITATIONS ! Votre dépôt a été validé !**\n\n" if congrats else ""
    bot.send_message(
        chat_id,
        f"{prefix}Cliquez sur le bouton ci-dessous pour lancer votre application :",
        reply_markup=markup
    )

# ---------------------------------------------------------
# 9. PAIEMENTS TELEGRAM STARS & OUTILS ADMIN
# ---------------------------------------------------------
@bot.callback_query_handler(func=lambda call: call.data == "pay_stars")
def process_stars_payment(call):
    bot.answer_callback_query(call.id)
    prices = [LabeledPrice(label="Abonnement VIP 30 Jours", amount=250)]
    bot.send_invoice(
        call.message.chat.id,
        title="Accès VIP 30 Jours",
        description="Accès illimité aux Mini Apps.",
        invoice_payload="vip_access_payload",
        provider_token=PAYMENT_PROVIDER_TOKEN,
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
    expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT chosen_universe FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        universe = row["chosen_universe"] if row and row["chosen_universe"] else "trading"

        cursor.execute("UPDATE users SET status = 'VIP', vip_expiry = ?, funnel_step = 'COMPLETED' WHERE user_id = ?", (expiry, user_id))
        conn.commit()

    send_app_access(msg.chat.id, universe, congrats=True)

@bot.message_handler(commands=["notify_update"])
def notify_update(msg):
    """Notification globale à tous les utilisateurs actuels du bot."""
    if msg.from_user.id != ADMIN_ID:
        return

    text = (
        "🚀 **MISE À JOUR DE VOTRE BOT !**\n\n"
        "Notre bot a été entièrement repensé pour vous offrir une expérience fluide :\n"
        "✅ Mini-Apps Trading et Pronostics séparées et optimisées\n"
        "✅ Suivi automatique des prix crypto et des matchs du jour dans le canal\n"
        "✅ Nouveau système de parrainage (3 amis = 1 mois VIP offert !)\n\n"
        "👉 Tapez /start pour découvrir la nouvelle version !"
    )

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()

    success, fail = 0, 0
    for u in users:
        try:
            bot.send_message(u["user_id"], text)
            success += 1
            time.sleep(0.04)
        except Exception:
            fail += 1

    bot.reply_to(msg, f"📊 **Rapport de diffusion :**\n✅ Envoyés : {success}\n❌ Échecs : {fail}")

@bot.message_handler(commands=["stats"])
def show_stats(msg):
    """Affichage des statistiques réelles et exactes."""
    if msg.from_user.id != ADMIN_ID:
        return

    thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as total FROM users")
        total_users = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) as monthly FROM users WHERE last_active >= ?", (thirty_days_ago,))
        monthly_users = cursor.fetchone()["monthly"]

        cursor.execute("SELECT COUNT(*) as vip FROM users WHERE status = 'VIP'")
        vip_users = cursor.fetchone()["vip"]

    text = (
        "📈 **STATISTIQUES REELLES DU BOT**\n\n"
        f"👥 **Utilisateurs inscrits :** `{total_users}`\n"
        f"🔥 **Actifs ce mois-ci (30j) :** `{monthly_users}`\n"
        f"⭐ **Membres VIP actuels :** `{vip_users}`"
    )
    bot.reply_to(msg, text)

# ---------------------------------------------------------
# 10. SCHEDULER DE TÂCHES AUTOMATIQUES
# ---------------------------------------------------------
def send_funnel_reminders():
    """Rappels personnalisés à 6h, 12h et 18h."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, chosen_universe FROM users WHERE status = 'FREE' AND funnel_step != 'COMPLETED'")
        rows = cursor.fetchall()

        for row in rows:
            user_id = row["user_id"]
            universe = row["chosen_universe"]

            if universe == "trading":
                msg = "⏳ **Rappel Trading :** N'oubliez pas d'effectuer votre dépôt de 10$ sur Exness/KuCoin pour débloquer votre accès VIP !"
            elif universe == "betting":
                msg = "⏳ **Rappel Pronostics :** N'oubliez pas d'effectuer votre dépôt de 5$ sur 1xBet/MelBet pour débloquer vos prédictions !"
            else:
                msg = "⏳ **N'attendez plus !** Rejoignez-nous et choisissez votre domaine (Trading ou Pronostics) dès maintenant."

            try:
                bot.send_message(user_id, msg)
                time.sleep(0.05)
            except Exception:
                pass

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(send_funnel_reminders, 'cron', hour='6,12,18')
scheduler.add_job(publish_crypto_update, 'interval', hours=4)
scheduler.add_job(publish_daily_matches, 'cron', hour=8)
scheduler.start()

# ---------------------------------------------------------
# 11. DÉMARRAGE DU BOT & SERVEUR FLASK
# ---------------------------------------------------------
app = Flask("")

@app.route("/")
def home():
    return "Bot en ligne", 200

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

if __name__ == "__main__":
    keep_alive()
    logger.info("MegaBot V2 100% Intégré Démarré avec succès !")
    bot.infinity_polling(none_stop=True, skip_pending=True)