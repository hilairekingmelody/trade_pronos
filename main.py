import os
import sys
import logging
import sqlite3
from datetime import datetime, timedelta
from threading import Thread

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.background import BackgroundScheduler

# ---------------------------------------------------------
# 1. CONFIGURATION & LOGS
# ---------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TradingBot")

TOKEN = os.environ.get("BOT_TOKEN", "").strip() or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "@trading_pronos").strip()
ADMIN_ID_RAW = os.environ.get("ADMIN_ID", "0").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown") if TOKEN else None
URL_MINI_APP = os.environ.get("URL_MINI_APP_TRADING", "https://trading-3wcr.onrender.com")

# Liens d'affiliation Exness & KuCoin
EXNESS_LINK = "https://one.exnessonelink.com/a/395vyusacl"
EXNESS_PROMO = "395vyusacl"
KUCOIN_LINK = "https://www.kucoin.com/ucenter/signup?&rcode=rEN8V1E&utm_medium=U17710"
KUCOIN_PROMO = "rEN8V1E"

user_states = {}
user_temp_data = {}

# ---------------------------------------------------------
# 2. BASE DE DONNÉES SQLITE
# ---------------------------------------------------------
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

# ---------------------------------------------------------
# 3. VERIFICATION MEMBRE CANAL (FORCE JOIN)
# ---------------------------------------------------------
def check_channel_membership(user_id: int) -> bool:
    if not CHANNEL_ID or not bot or user_id == ADMIN_ID:
        return True
    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["creator", "administrator", "member"]
    except Exception as e:
        logger.error(f"Erreur vérification canal : {e}")
        return False

# ---------------------------------------------------------
# 4. BOT TELEGRAM, PARCOURS & ADMIN
# ---------------------------------------------------------
if bot:

    @bot.message_handler(commands=["start"])
    def start_cmd(msg):
        u_id = msg.from_user.id
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
        if check_channel_membership(call.from_user.id):
            bot.answer_callback_query(call.id, "✅ Accès validé !")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            send_main_menu(call.message.chat.id, call.from_user.username or "Utilisateur", call.from_user.id)
        else:
            bot.answer_callback_query(call.id, "❌ Vous n'avez pas encore rejoint le canal !", show_alert=True)

    def send_main_menu(chat_id, u_name, u_id):
        is_verified = False
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT status FROM users WHERE user_id = ?", (u_id,))
            row = c.fetchone()
            if row and (row["status"] == "VERIFIED" or u_id == ADMIN_ID):
                is_verified = True

        markup = InlineKeyboardMarkup(row_width=1)

        if is_verified:
            markup.add(InlineKeyboardButton("📈 Ouvrir la Mini App Trading", web_app=telebot.types.WebAppInfo(url=URL_MINI_APP)))
            text = (
                f"Bienvenue *{u_name}* sur le Terminal de Trading !\n\n"
                "✅ **Votre compte est vérifié.** Vous pouvez accéder gratuitement à tous les signaux et fonctionnalités de la Mini App ci-dessous :"
            )
        else:
            markup.add(InlineKeyboardButton("🔒 Mini App Verrouillée (Vérification requise)", callback_data="locked_app"))
            markup.add(InlineKeyboardButton("📊 Inscription Exness (Promo: 395vyusacl)", url=EXNESS_LINK))
            markup.add(InlineKeyboardButton("📊 Inscription KuCoin (Promo: rEN8V1E)", url=KUCOIN_LINK))
            markup.add(InlineKeyboardButton("📥 Envoyer Preuves de Dépôt (10$ min)", callback_data="submit_proof"))

            text = (
                f"Bienvenue *{u_name}* sur le Terminal de Trading.\n\n"
                "🔹 **Accès à la Mini App :** Pour débloquer l'accès complet et gratuit aux signaux IA, vous devez effectuer un dépôt minimum de **10$** sur Exness ou KuCoin via nos liens partenaires et soumettre votre preuve.\n\n"
                "⚠️ *La Mini App reste inaccessible tant que votre compte n'a pas été validé par un administrateur.*"
            )

        bot.send_message(chat_id, text, reply_markup=markup)

    @bot.callback_query_handler(func=lambda c: c.data == "locked_app")
    def callback_locked_app(call):
        bot.answer_callback_query(call.id, "🔒 Accès refusé ! Envoyez d'abord vos preuves de dépôt pour que l'administrateur valide votre compte.", show_alert=True)

    # ---------------------------------------------------------
    # PANNEAU ADMIN (/admin, /grant, /revoke)
    # ---------------------------------------------------------
    @bot.message_handler(commands=["admin"])
    def admin_cmd(msg):
        if msg.from_user.id != ADMIN_ID and ADMIN_ID != 0:
            bot.reply_to(msg, "❌ Accès refusé. Vous n'êtes pas l'administrateur.")
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

        text += "\n*Gestion manuelle des accès :*\n`/grant <user_id>` : Valider l'accès\n`/revoke <user_id>` : Bloquer l'accès"
        bot.send_message(msg.chat.id, text)

    @bot.message_handler(commands=["grant"])
    def grant_access(msg):
        if msg.from_user.id != ADMIN_ID and ADMIN_ID != 0:
            return
        try:
            target_id = int(msg.text.split()[1])
            with get_db() as conn:
                conn.cursor().execute("UPDATE users SET status = 'VERIFIED', linked_account = 'MANUAL' WHERE user_id = ?", (target_id,))
                conn.commit()
            bot.reply_to(msg, f"✅ L'utilisateur `{target_id}` a été validé. Accès Mini App débloqué.")

            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("📈 Ouvrir la Mini App Trading", web_app=telebot.types.WebAppInfo(url=URL_MINI_APP)))
            bot.send_message(target_id, "🎉 **Accès Débloqué !** Votre compte a été validé par l'administrateur. Vous pouvez désormais ouvrir la Mini App.", reply_markup=markup)
        except Exception:
            bot.reply_to(msg, "❌ Format incorrect. Utilisation : `/grant 12345678`")

    @bot.message_handler(commands=["revoke"])
    def revoke_access(msg):
        if msg.from_user.id != ADMIN_ID and ADMIN_ID != 0:
            return
        try:
            target_id = int(msg.text.split()[1])
            with get_db() as conn:
                conn.cursor().execute("UPDATE users SET status = 'PENDING', linked_account = NULL WHERE user_id = ?", (target_id,))
                conn.commit()
            bot.reply_to(msg, f"🚫 Accès révoqué pour l'utilisateur `{target_id}`.")
            bot.send_message(target_id, "⚠️ Votre accès à la Mini App a été suspendu par l'administrateur.")
        except Exception:
            bot.reply_to(msg, "❌ Format incorrect. Utilisation : `/revoke 12345678`")

    # ---------------------------------------------------------
    # SOUMISSION PREUVES & VALIDATION ADMIN
    # ---------------------------------------------------------
    @bot.callback_query_handler(func=lambda c: c.data == "submit_proof")
    def submit_proof_start(call):
        user_states[call.message.chat.id] = "WAIT_ID"
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "📝 **Étape 1/2 :** Entrez votre ID de compte Exness ou KuCoin :")

    @bot.message_handler(func=lambda m: user_states.get(m.chat.id) == "WAIT_ID")
    def process_proof_id(msg):
        user_temp_data[msg.chat.id] = {"account_id": msg.text.strip()}
        user_states[msg.chat.id] = "WAIT_PHOTO"
        bot.reply_to(msg, "📸 **Étape 2/2 :** Envoyez la capture d'écran de votre dépôt de 10$ minimum.")

    @bot.message_handler(content_types=["photo"], func=lambda m: user_states.get(m.chat.id) == "WAIT_PHOTO")
    def process_proof_photo(msg):
        chat_id = msg.chat.id
        u_id = msg.from_user.id
        photo_id = msg.photo[-1].file_id
        acc_id = user_temp_data.get(chat_id, {}).get("account_id", "Non spécifié")

        with get_db() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO pending_validations (user_id, account_id, photo_id, created_at) VALUES (?, ?, ?, ?)",
                      (u_id, acc_id, photo_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            req_id = c.lastrowid
            c.execute("UPDATE users SET funnel_step = 'PROOFS_SENT' WHERE user_id = ?", (u_id,))
            conn.commit()

        user_states[chat_id] = None
        bot.reply_to(msg, "✅ **Preuve reçue.** L'administrateur va vérifier les informations transmises.")

        if ADMIN_ID != 0:
            mk = InlineKeyboardMarkup()
            mk.add(
                InlineKeyboardButton("✅ Valider l'accès", callback_data=f"adm_ok_{req_id}"),
                InlineKeyboardButton("❌ Rejeter", callback_data=f"adm_no_{req_id}")
            )
            bot.send_photo(ADMIN_ID, photo_id, caption=f"🔔 **DEMANDE DE VALIDATION D'ACCÈS**\nUser ID: `{u_id}`\nID Compte: `{acc_id}`", reply_markup=mk)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_ok_"))
    def admin_approve(call):
        if call.from_user.id != ADMIN_ID and ADMIN_ID != 0:
            return
        req_id = int(call.data.replace("adm_ok_", ""))
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT user_id, account_id FROM pending_validations WHERE id = ?", (req_id,))
            row = c.fetchone()
            if row:
                u_id = row["user_id"]
                c.execute("UPDATE users SET status = 'VERIFIED', linked_account = ?, funnel_step = 'COMPLETED' WHERE user_id = ?", (row["account_id"], u_id))
                c.execute("UPDATE pending_validations SET status = 'APPROVED' WHERE id = ?", (req_id,))
                conn.commit()
                bot.edit_message_caption(caption="✅ **Demande Approuvée avec succès.**", chat_id=call.message.chat.id, message_id=call.message.message_id)

                mk = InlineKeyboardMarkup()
                mk.add(InlineKeyboardButton("📈 Ouvrir la Mini App Trading", web_app=telebot.types.WebAppInfo(url=URL_MINI_APP)))
                bot.send_message(u_id, "🎉 **Félicitations ! Votre compte a été validé !**\nVotre accès complet à la Mini App Trading est débloqué.", reply_markup=mk)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("adm_no_"))
    def admin_reject(call):
        if call.from_user.id != ADMIN_ID and ADMIN_ID != 0:
            return
        req_id = int(call.data.replace("adm_no_", ""))
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT user_id FROM pending_validations WHERE id = ?", (req_id,))
            row = c.fetchone()
            if row:
                u_id = row["user_id"]
                c.execute("UPDATE pending_validations SET status = 'REJECTED' WHERE id = ?", (req_id,))
                conn.commit()
                bot.edit_message_caption(caption="❌ **Demande Rejetée.**", chat_id=call.message.chat.id, message_id=call.message.message_id)
                bot.send_message(u_id, "❌ **Votre demande a été refusée.** Preuve invalide. Veuillez soumettre une preuve valide avec un dépôt minimum de 10$.")

# ---------------------------------------------------------
# 5. AUTOMATISATION (RELANCES 3H & ALERTES CANAL 1H)
# ---------------------------------------------------------
def run_reminders_3h():
    if not bot: return
    limit_time = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE status = 'PENDING' AND funnel_step IN ('STARTED', 'WAIT_ID', 'WAIT_PHOTO') AND last_active <= ? AND last_reminder_sent IS NULL", (limit_time,))
        users = c.fetchall()
        for u in users:
            try:
                mk = InlineKeyboardMarkup(row_width=1)
                mk.add(InlineKeyboardButton("📥 Envoyer Preuves de Dépôt (10$)", callback_data="submit_proof"))
                bot.send_message(
                    u["user_id"],
                    "⏰ **RAPPEL : Finalisez votre accès au Terminal IA !**\n\nVous n'avez pas terminé votre inscription. Effectuez un dépôt de 10$ minimum et transmettez votre preuve pour débloquer la Mini App gratuitement !",
                    reply_markup=mk
                )
                c.execute("UPDATE users SET last_reminder_sent = ? WHERE user_id = ?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), u["user_id"]))
            except Exception as e:
                logger.error(f"Erreur relance 3h pour {u['user_id']}: {e}")
        conn.commit()

def run_market_alerts():
    if not bot or not CHANNEL_ID: return
    try:
        cg = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true", timeout=10).json()
        fx = requests.get("https://api.frankfurter.app/latest?from=EUR&to=USD", timeout=10).json()

        btc_p, btc_c = cg["bitcoin"]["usd"], cg["bitcoin"]["usd_24h_change"]
        eth_p, eth_c = cg["ethereum"]["usd"], cg["ethereum"]["usd_24h_change"]
        eur_usd = fx["rates"]["USD"]

        text = (
            "📊 **ALERTES MARCHÉS EN TEMPS RÉEL**\n\n"
            f"🪙 **BTC/USD :** `{btc_p:,.2f} $` ({'🟢' if btc_c >= 0 else '🔴'} {btc_c:+.2f}%)\n"
            f"💎 **ETH/USD :** `{eth_p:,.2f} $` ({'🟢' if eth_c >= 0 else '🔴'} {eth_c:+.2f}%)\n"
            f"💱 **EUR/USD :** `{eur_usd:.4f}`\n\n"
            "📈 *Ouvrez la Mini App pour obtenir vos signaux prédictifs IA.*"
        )
        bot.send_message(CHANNEL_ID, text)
    except Exception as e:
        logger.error(f"Erreur alerte marché : {e}")

sched = BackgroundScheduler(daemon=True)
sched.add_job(run_reminders_3h, 'interval', minutes=15)
sched.add_job(run_market_alerts, 'interval', hours=1)
sched.start()

# ---------------------------------------------------------
# 6. SERVEUR FLASK (RENDER & ENDPOINTS WEBAPP)
# ---------------------------------------------------------
app = Flask(__name__)
CORS(app)

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "Trading Bot Web Service"}), 200

@app.route("/api/user-status", methods=["POST"])
def user_status():
    data = request.json or {}
    u_id = int(data.get("userId", 0))
    if not u_id: return jsonify({"error": "userId obligatoire"}), 400

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
    admin_id = int(data.get("adminId", 0))
    target_id = int(data.get("targetId", 0))
    action = data.get("action")

    if admin_id != ADMIN_ID and ADMIN_ID != 0:
        return jsonify({"success": False, "error": "Accès refusé"}), 403

    with get_db() as conn:
        c = conn.cursor()
        if action == "grant":
            c.execute("UPDATE users SET status = 'VERIFIED', linked_account = 'MANUAL' WHERE user_id = ?", (target_id,))
            conn.commit()
            if bot:
                try:
                    bot.send_message(target_id, "🎉 **Accès Débloqué par l'administrateur !**")
                except Exception:
                    pass
            return jsonify({"success": True, "message": f"Accès accordé à {target_id}."})
        elif action == "revoke":
            c.execute("UPDATE users SET status = 'PENDING', linked_account = NULL WHERE user_id = ?", (target_id,))
            conn.commit()
            if bot:
                try:
                    bot.send_message(target_id, "⚠️ Votre accès a été révoqué par l'administrateur.")
                except Exception:
                    pass
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