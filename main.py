import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from threading import Thread

from flask import Flask
import pandas as pd
import requests
import ta
import telebot
from telebot.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    PreCheckoutQuery,
)

# ---------------------------------------------------------
# 1. SERVEUR WEB (Keep Alive Render)
# ---------------------------------------------------------
app = Flask("")


@app.route("/")
def home():
    return "Bot Telegram est EN LIGNE !", 200


def run_flask():
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()


# ---------------------------------------------------------
# 2. CONFIGURATION & SECRETS
# ---------------------------------------------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().replace('"', "").replace("'", "")
FOOTBALL_KEY = os.environ.get("FOOTBALL_API_KEY", "").strip() or os.environ.get("API_FOOTBALL", "").strip()

ADMIN_ID_RAW = os.environ.get("ADMIN_ID", "0").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0

if not TOKEN:
    raise ValueError("❌ ERREUR: La variable TELEGRAM_BOT_TOKEN est introuvable !")

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
WARN = "\n\n⚠️ *Attention :* Gestion de risque obligatoire. Interdit aux -18 ans."

user_states = {}
user_last_request_time = {}
ANTI_SPAM_DELAY = 3.0
DAILY_LIMIT_FREE = 5


# ---------------------------------------------------------
# 3. BASE DE DONNÉES SQLITE (Quotas & Abonnements 30 jours)
# ---------------------------------------------------------
DB_FILE = "users.db"


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            status TEXT DEFAULT 'FREE',
            daily_requests INTEGER DEFAULT 0,
            last_request_date TEXT,
            vip_expiry TEXT
        )
    """)
    conn.commit()
    conn.close()


def check_and_consume_quota(user_id):
    """
    Vérifie et consomme 1 requête.
    Retourne (Autorisé: bool, Message/Quota: str)
    """
    if user_id == ADMIN_ID:
        return True, "ADMIN"

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT status, daily_requests, last_request_date, vip_expiry FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()

    today_str = datetime.now().strftime("%Y-%m-%d")

    if not row:
        cursor.execute(
            "INSERT INTO users (user_id, status, daily_requests, last_request_date, vip_expiry) VALUES (?, 'FREE', 1, ?, NULL)",
            (user_id, today_str),
        )
        conn.commit()
        conn.close()
        return True, f"FREE (1/{DAILY_LIMIT_FREE})"

    status, requests_count, last_date, vip_expiry = row[0], row[1], row[2], row[3]

    # Vérification de l'expiration du statut PREMIUM / VIP (30 jours)
    if status in ["PREMIUM", "VIP"]:
        if vip_expiry:
            expiry_dt = datetime.strptime(vip_expiry, "%Y-%m-%d %H:%M:%S")
            if datetime.now() > expiry_dt:
                # Abonnement expiré ! Rétrogradation en FREE
                status = "FREE"
                cursor.execute("UPDATE users SET status = 'FREE', vip_expiry = NULL WHERE user_id = ?", (user_id,))
                conn.commit()
            else:
                conn.close()
                return True, "VIP Illimité"
        else:
            conn.close()
            return True, "VIP Illimité"

    # Réinitialisation si c'est le premier appel d'un nouveau jour
    if last_date != today_str:
        cursor.execute(
            "UPDATE users SET daily_requests = 1, last_request_date = ? WHERE user_id = ?",
            (today_str, user_id),
        )
        conn.commit()
        conn.close()
        return True, f"FREE (1/{DAILY_LIMIT_FREE})"

    # Si le quota journalier est atteint
    if requests_count >= DAILY_LIMIT_FREE:
        conn.close()
        return False, f"FREE ({requests_count}/{DAILY_LIMIT_FREE})"

    # Sinon incrémentation
    new_count = requests_count + 1
    cursor.execute("UPDATE users SET daily_requests = ? WHERE user_id = ?", (new_count, user_id))
    conn.commit()
    conn.close()
    return True, f"FREE ({new_count}/{DAILY_LIMIT_FREE})"


init_db()


# ---------------------------------------------------------
# 4. SÉCURITÉ & ANTI-SPAM STRICT
# ---------------------------------------------------------
def check_rate_limit(user_id):
    now = time.time()
    last_time = user_last_request_time.get(user_id, 0)
    if now - last_time < ANTI_SPAM_DELAY:
        return False
    user_last_request_time[user_id] = now
    return True


def is_admin(user_id):
    return user_id == ADMIN_ID


def sanitize_symbol(symbol):
    cleaned = re.sub(r"[^A-Z0-9]", "", symbol.upper())
    return cleaned if 2 <= len(cleaned) <= 12 else None


# ---------------------------------------------------------
# 5. FIX BINANCE & INDICATEURS CRYPTO
# ---------------------------------------------------------
def requete_binance_securisee(url_path):
    domaines = [
        "https://api1.binance.com",
        "https://api2.binance.com",
        "https://api3.binance.com",
        "https://data-api.binance.vision"
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

    for base_url in domaines:
        try:
            res = requests.get(f"{base_url}{url_path}", headers=headers, timeout=5)
            if res.status_code == 200:
                return res.json()
        except Exception:
            continue
    return None


def analyser_crypto(pair):
    pair_clean = sanitize_symbol(pair)
    if not pair_clean:
        return "❌ Symbole invalide. Exemple valide : `BTCUSDT`."

    url_path = f"/api/v3/klines?symbol={pair_clean}&interval=1h&limit=100"
    data = requete_binance_securisee(url_path)

    if not data:
        return f"❌ Paire `{pair_clean}` introuvable ou indisponible actuellement."

    try:
        closes = pd.Series([float(c[4]) for c in data])
        price = closes.iloc[-1]
        rsi = ta.momentum.RSIIndicator(closes, window=14).rsi().iloc[-1]
        macd_indicator = ta.trend.MACD(closes)
        macd_diff = macd_indicator.macd_diff().iloc[-1]

        ema20 = ta.trend.EMAIndicator(closes, window=20).ema_indicator().iloc[-1]
        ema50 = ta.trend.EMAIndicator(closes, window=50).ema_indicator().iloc[-1]

        score = 0
        if rsi < 35:
            score += 2
        elif rsi < 45:
            score += 1
        elif rsi > 65:
            score -= 2
        elif rsi > 55:
            score -= 1

        if macd_diff > 0:
            score += 1
        else:
            score -= 1

        if price > ema20 > ema50:
            score += 2
        elif price < ema20 < ema50:
            score -= 2

        if score >= 3:
            signal = "🚀 **ACHAT FORT (BULLISH)**"
        elif score >= 1:
            signal = "🟢 **ACHAT POTENTIEL**"
        elif score <= -3:
            signal = "💥 **VENTE FORTE (BEARISH)**"
        elif score <= -1:
            signal = "🔴 **VENTE POTENTIELLE**"
        else:
            signal = "🟡 **NEUTRE / CONSOLIDATION**"

        trend = "📈 Haussière" if price > ema50 else "📉 Baissière"

        return (
            f"📊 **ANALYSE TEMPS RÉEL : {pair_clean}**\n\n"
            f"💰 **Prix actuel :** `{price:,.4f} $`\n"
            f"📈 **Tendance globale :** {trend}\n"
            f"🔹 **RSI (14) :** `{rsi:.1f}`\n"
            f"🔹 **MACD Hist :** `{macd_diff:.4f}`\n"
            f"🔹 **EMA 20 / 50 :** `{ema20:,.2f} / {ema50:,.2f}`\n\n"
            f"🎯 **Signal détecté :** {signal}"
            f"{WARN}"
        )
    except Exception:
        return "❌ Erreur de calcul des indicateurs."


def executer_backtest(pair):
    pair_clean = sanitize_symbol(pair)
    if not pair_clean:
        return "❌ Symbole invalide pour le backtest."

    url_path = f"/api/v3/klines?symbol={pair_clean}&interval=1h&limit=500"
    data = requete_binance_securisee(url_path)

    if not data:
        return f"❌ Paire `{pair_clean}` indisponible pour le backtest."

    try:
        closes = pd.Series([float(c[4]) for c in data])
        rsi_series = ta.momentum.RSIIndicator(closes).rsi()

        wins, total = 0, 0
        for i in range(30, len(closes) - 1):
            r = rsi_series.iloc[i]
            if r < 35:
                total += 1
                if closes.iloc[i + 1] > closes.iloc[i]:
                    wins += 1
            elif r > 65:
                total += 1
                if closes.iloc[i + 1] < closes.iloc[i]:
                    wins += 1

        winrate = (wins / total * 100) if total > 0 else 0
        return (
            f"📈 **BACKTEST STRATÉGIE (500 Bougies 1H)**\n\n"
            f"🪙 **Paire :** `{pair_clean}`\n"
            f"🔢 **Signaux exécutés :** `{total}`\n"
            f"✅ **Trades gagnants :** `{wins}`\n"
            f"📊 **Taux de réussite :** `{winrate:.1f}%`"
            f"{WARN}"
        )
    except Exception:
        return "❌ Erreur de calcul lors du backtest."


# ---------------------------------------------------------
# 6. FOOTBALL
# ---------------------------------------------------------
def obtenir_analyses_matchs():
    url = "https://api.football-data.org/v4/matches"
    headers = {"X-Auth-Token": FOOTBALL_KEY}

    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code != 200:
            return "⚽ Données football temporairement indisponibles."

        data = response.json()
        matches = data.get("matches", [])[:3]

        if not matches:
            return "⚽ Aucun gros match au programme aujourd'hui."

        message = "⚽ **ANALYSES DES 3 GROS MATCHS DU JOUR** ⚽\n\n"
        for idx, match in enumerate(matches, 1):
            equipe_dom = match['homeTeam']['name']
            equipe_ext = match['awayTeam']['name']
            competition = match['competition']['name']

            message += f"**{idx}. {equipe_dom} vs {equipe_ext}** ({competition})\n"
            message += f"📊 *Analyse :* Avantage à domicile pour {equipe_dom}.\n"
            message += f"💡 *Pronostic :* Plus de 1.5 buts / Victoire ou Nul {equipe_dom}\n\n"

        return message
    except Exception:
        return "⚠️ Service football indisponible actuellement."


# ---------------------------------------------------------
# 7. COMMANDES GRATUITES (Ne consomment PAS de quota)
# ---------------------------------------------------------
@bot.message_handler(commands=["start"])
def send_welcome(msg):
    user_states[msg.chat.id] = None
    text = (
        "👋 **Bienvenue sur TRADING & PRONOSTICS BOT !**\n\n"
        "👑 **Membre Gratuit :** 5 requêtes d'analyse offertes par jour.\n"
        "⭐ **Pass VIP (30 Jours) :** Accès illimité 24/7 ! Tapez `/vip`."
    )
    bot.reply_to(msg, text)


@bot.message_handler(commands=["help", "cmds"])
def send_help(msg):
    user_states[msg.chat.id] = None
    text = (
        "🤖 **MegaBot Trading & Sport v3.1**\n\n"
        "📈 `/crypto` : Analyses crypto en temps réel (1 req)\n"
        "📉 `/backtest` : Simulation stratégie 500 bougies (1 req)\n"
        "⚽ `/pari` : Pronostics football du jour (1 req)\n"
        "💰 `/bankroll` : Gestion de capital 2% (1 req)\n"
        "⭐ `/vip` : S'abonner pour 30 jours illimités (Gratuit)\n"
        f"{WARN}"
    )
    bot.reply_to(msg, text)


@bot.message_handler(commands=["vip", "premium", "buy"])
def command_vip(msg):
    user_states[msg.chat.id] = None
    markup = InlineKeyboardMarkup()
    btn_stars = InlineKeyboardButton("⭐ S'abonner VIP 30 Jours (250 Stars)", callback_data="buy_vip_stars")
    markup.add(btn_stars)

    txt = (
        "👑 **PASS VIP 30 JOURS - TELEGRAM STARS**\n\n"
        "Débloquez l'accès total pendant **30 jours** :\n"
        "✅ **Analyses Crypto ILLIMITÉES**\n"
        "✅ **Backtests ILLIMITÉS**\n"
        "✅ **Pronostics Football Quotidiens**\n\n"
        "Prix : **250 Telegram Stars / mois**\n"
        "Cliquez ci-dessous pour lancer le paiement sécurisé :"
    )
    bot.reply_to(msg, txt, reply_markup=markup)


@bot.message_handler(commands=["admin"])
def cmd_admin(msg):
    user_states[msg.chat.id] = None
    if not is_admin(msg.from_user.id):
        bot.reply_to(msg, "⛔ **Accès refusé.** Réservé à l'administrateur.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), SUM(CASE WHEN status='PREMIUM' THEN 1 ELSE 0 END) FROM users")
    row = cursor.fetchone()
    total_users, premium_users = row[0], row[1] or 0
    conn.close()

    txt = (
        "⚙️ **PANNEAU D'ADMINISTRATION**\n\n"
        f"🆔 **ID Admin :** `{ADMIN_ID}`\n"
        f"👥 **Utilisateurs Totaux :** `{total_users}`\n"
        f"⭐ **Membres VIP Actifs :** `{premium_users}`\n\n"
        "💡 *Accorder 30 jours VIP manuellement :*\n"
        "`/grant_premium <user_id>`"
    )
    bot.reply_to(msg, txt)


@bot.message_handler(commands=["grant_premium"])
def cmd_grant_premium(msg):
    if not is_admin(msg.from_user.id):
        return

    parts = msg.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(msg, "❌ Format correct : `/grant_premium <user_id>`")
        return

    target_id = int(parts[1])
    expiry_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET status = 'PREMIUM', vip_expiry = ? WHERE user_id = ?", (expiry_date, target_id))
    conn.commit()
    conn.close()

    bot.reply_to(msg, f"✅ L'utilisateur `{target_id}` est **PREMIUM pour 30 jours** (Jusqu'au {expiry_date}) !")


# ---------------------------------------------------------
# 8. COMMANDES PAYANTES (Consomment 1 requête chacune)
# ---------------------------------------------------------
@bot.message_handler(commands=["crypto"])
def command_crypto(msg):
    user_states[msg.chat.id] = None
    if not check_rate_limit(msg.from_user.id):
        return bot.reply_to(msg, "⏳ *Anti-Spam :* Veuillez patienter 3 secondes entre vos clics.")

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🟡 BTC/USDT", callback_data="c_BTCUSDT"),
        InlineKeyboardButton("🔹 ETH/USDT", callback_data="c_ETHUSDT"),
        InlineKeyboardButton("☀️ SOL/USDT", callback_data="c_SOLUSDT"),
        InlineKeyboardButton("🔶 BNB/USDT", callback_data="c_BNBUSDT"),
        InlineKeyboardButton("🌐 XRP/USDT", callback_data="c_XRPUSDT"),
        InlineKeyboardButton("✍️ Autre symbole...", callback_data="c_custom")
    )

    bot.reply_to(msg, "📈 **ANALYSE CRYPTO EN TEMPS RÉEL**\n\nSélectionnez une paire :", reply_markup=markup)


@bot.message_handler(commands=["backtest"])
def command_backtest(msg):
    user_states[msg.chat.id] = None
    if not check_rate_limit(msg.from_user.id):
        return bot.reply_to(msg, "⏳ *Anti-Spam :* Veuillez patienter 3 secondes entre vos clics.")

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🟡 BTC/USDT", callback_data="bt_BTCUSDT"),
        InlineKeyboardButton("🔹 ETH/USDT", callback_data="bt_ETHUSDT"),
        InlineKeyboardButton("☀️ SOL/USDT", callback_data="bt_SOLUSDT"),
        InlineKeyboardButton("✍️ Autre symbole...", callback_data="bt_custom")
    )

    bot.reply_to(msg, "📉 **BACKTEST STRATÉGIE**\n\nSélectionnez une paire :", reply_markup=markup)


@bot.message_handler(commands=['pari', 'paris'])
def handle_paris(msg):
    user_states[msg.chat.id] = None
    if not check_rate_limit(msg.from_user.id):
        return bot.reply_to(msg, "⏳ *Anti-Spam :* Veuillez patienter 3 secondes entre vos clics.")

    allowed, quota_info = check_and_consume_quota(msg.from_user.id)
    if not allowed:
        return bot.reply_to(
            msg,
            "❌ **Quota Quotidien Atteint !**\n\nVous avez consommé vos 5 requêtes gratuites du jour.\n👉 Tapez `/vip` pour vous abonner et débloquer l'accès **ILLIMITÉ** !"
        )

    bot.send_chat_action(msg.chat.id, 'typing')
    bot.reply_to(msg, f"{obtenir_analyses_matchs()}\n\n📊 *Consommation :* `{quota_info}`")


@bot.message_handler(commands=["bankroll"])
def bankroll_start(msg):
    if not check_rate_limit(msg.from_user.id):
        return bot.reply_to(msg, "⏳ *Anti-Spam :* Veuillez patienter 3 secondes entre vos clics.")

    user_states[msg.chat.id] = "WAITING_BANKROLL"
    bot.reply_to(msg, "💰 **GESTION DE CAPITAL (2%)**\n\nEntrez votre capital total (ex: `5000`) :")


# ---------------------------------------------------------
# 9. PAIEMENTS TELEGRAM STARS (Fix du blocage /vip)
# ---------------------------------------------------------
@bot.callback_query_handler(func=lambda call: call.data == "buy_vip_stars")
def process_buy_stars(call):
    # Payload unique généré à chaque clic avec timestamp pour éviter le blocage
    payload_unique = f"vip_sub_{call.from_user.id}_{int(time.time())}"

    bot.send_invoice(
        call.message.chat.id,
        title="Pass VIP 30 Jours",
        description="Accès illimité aux signaux Trading Crypto, Backtests et Pronostics Sportifs pendant 30 jours.",
        invoice_payload=payload_unique,
        provider_token="",  # Laisser vide pour Telegram Stars
        currency="XTR",     # Code devise officiel pour Telegram Stars
        prices=[LabeledPrice("Abonnement 1 Mois", 250)]
    )


@bot.pre_checkout_query_handler(func=lambda query: True)
def process_pre_checkout(query: PreCheckoutQuery):
    bot.answer_pre_checkout_query(query.id, ok=True)


@bot.message_handler(content_types=['successful_payment'])
def process_successful_payment(msg):
    user_id = msg.from_user.id
    expiry_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET status = 'PREMIUM', vip_expiry = ? WHERE user_id = ?", (expiry_date, user_id))
    conn.commit()
    conn.close()

    bot.reply_to(
        msg,
        f"🎉 **FÉLICITATIONS ET BIENVENUE VIP !** 🎉\n\n"
        f"Votre abonnement de **30 Jours** a été activé avec succès !\n"
        f"📅 Valable jusqu'au : `{expiry_date}`"
    )


# ---------------------------------------------------------
# 10. GESTION DES CALLBACKS (Boutons Inline)
# ---------------------------------------------------------
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    if not check_rate_limit(user_id):
        bot.answer_callback_query(call.id, "⏳ Anti-Spam : Veuillez patienter 3 secondes...", show_alert=True)
        return

    if call.data.startswith("c_"):
        pair = call.data.replace("c_", "")
        if pair == "custom":
            user_states[chat_id] = "WAITING_CRYPTO_PAIR"
            bot.send_message(chat_id, "🔍 Tapez le nom de la paire à analyser (ex: `ADAUSDT`) :")
        else:
            allowed, quota_info = check_and_consume_quota(user_id)
            if not allowed:
                bot.send_message(
                    chat_id,
                    "❌ **Quota Quotidien Atteint !**\n\nVous avez consommé vos 5 requêtes gratuites du jour.\n👉 Tapez `/vip` pour vous abonner et débloquer l'accès **ILLIMITÉ** !"
                )
                return
            bot.answer_callback_query(call.id, "Analyse en cours...")
            bot.send_message(chat_id, f"{analyser_crypto(pair)}\n\n📊 *Consommation :* `{quota_info}`")

    elif call.data.startswith("bt_"):
        pair = call.data.replace("bt_", "")
        if pair == "custom":
            user_states[chat_id] = "WAITING_BACKTEST_PAIR"
            bot.send_message(chat_id, "🔍 Tapez le nom de la paire pour le backtest (ex: `DOGEUSDT`) :")
        else:
            allowed, quota_info = check_and_consume_quota(user_id)
            if not allowed:
                bot.send_message(
                    chat_id,
                    "❌ **Quota Quotidien Atteint !**\n\nVous avez consommé vos 5 requêtes gratuites du jour.\n👉 Tapez `/vip` pour vous abonner et débloquer l'accès **ILLIMITÉ** !"
                )
                return
            bot.answer_callback_query(call.id, "Backtest en cours...")
            bot.send_message(chat_id, f"{executer_backtest(pair)}\n\n📊 *Consommation :* `{quota_info}`")


# ---------------------------------------------------------
# 11. GESTION DU TEXTE LIBRE
# ---------------------------------------------------------
@bot.message_handler(func=lambda msg: not msg.text.startswith('/'))
def handle_text_messages(msg):
    chat_id = msg.chat.id
    user_id = msg.from_user.id
    state = user_states.get(chat_id)

    if not state:
        return

    if not check_rate_limit(user_id):
        return bot.reply_to(msg, "⏳ *Anti-Spam :* Veuillez patienter 3 secondes entre vos envois.")

    if state == "WAITING_BANKROLL":
        user_states[chat_id] = None
        allowed, quota_info = check_and_consume_quota(user_id)
        if not allowed:
            return bot.send_message(
                chat_id,
                "❌ **Quota Quotidien Atteint !**\n\nVous avez consommé vos 5 requêtes gratuites du jour.\n👉 Tapez `/vip` pour vous abonner et débloquer l'accès **ILLIMITÉ** !"
            )

        cleaned_text = msg.text.replace(" ", "").replace(",", ".").strip()
        try:
            capital = float(cleaned_text)
            if capital <= 0:
                bot.send_message(chat_id, "❌ Le capital doit être supérieur à zéro.")
                return

            mise = capital * 0.02
            res = (
                f"💼 **CALCUL DE RISQUE (2% STRICT)**\n\n"
                f"💵 **Capital :** `{capital:,.2f}`\n"
                f"🎯 **Mise max recommandée :** `{mise:,.2f}`\n\n"
                f"📊 *Consommation :* `{quota_info}`"
                f"{WARN}"
            )
            bot.send_message(chat_id, res)
        except ValueError:
            bot.send_message(chat_id, "❌ Nombre invalide. Réessayez avec `/bankroll`.")

    elif state == "WAITING_CRYPTO_PAIR":
        user_states[chat_id] = None
        allowed, quota_info = check_and_consume_quota(user_id)
        if not allowed:
            return bot.send_message(
                chat_id,
                "❌ **Quota Quotidien Atteint !**\n\nVous avez consommé vos 5 requêtes gratuites du jour.\n👉 Tapez `/vip` pour vous abonner et débloquer l'accès **ILLIMITÉ** !"
            )
        bot.send_message(chat_id, f"{analyser_crypto(msg.text.strip())}\n\n📊 *Consommation :* `{quota_info}`")

    elif state == "WAITING_BACKTEST_PAIR":
        user_states[chat_id] = None
        allowed, quota_info = check_and_consume_quota(user_id)
        if not allowed:
            return bot.send_message(
                chat_id,
                "❌ **Quota Quotidien Atteint !**\n\nVous avez consommé vos 5 requêtes gratuites du jour.\n👉 Tapez `/vip` pour vous abonner et débloquer l'accès **ILLIMITÉ** !"
            )
        bot.send_message(chat_id, f"{executer_backtest(msg.text.strip())}\n\n📊 *Consommation :* `{quota_info}`")


# ---------------------------------------------------------
# 12. DÉMARRAGE DU BOT
# ---------------------------------------------------------
if __name__ == "__main__":
    keep_alive()
    print("🤖 Bot Monétisé & Sécurisé prêt !")
    bot.infinity_polling(none_stop=True)