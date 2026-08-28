import os
import sys
import re
import time
import logging
import sqlite3
from datetime import datetime, timedelta
from threading import Thread
from typing import Tuple

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
# 1. LOGGING & SÉCURITÉ
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("TradingBot")

TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().replace('"', "").replace("'", "")
)
FOOTBALL_KEY = (
    os.environ.get("FOOTBALL_API_KEY", "").strip()
    or os.environ.get("API_FOOTBALL", "").strip()
)

ADMIN_ID_RAW = os.environ.get("ADMIN_ID", "0").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0

if not TOKEN:
    raise ValueError(
        "❌ ERREUR CRITIQUE: La variable TELEGRAM_BOT_TOKEN est introuvable !"
    )

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
WARN = "\n\n⚠️ *Attention :* Gestion du risque obligatoire. Interdit aux -18 ans."

# Textes de Cross-Selling Automatiques
CROSS_SELL_CRYPTO = "\n\n💡 *Exécutez ce trade avec -10% de frais sur notre exchange partenaire : Tapez /affiliation*"
CROSS_SELL_FOOT = "\n\n🎁 *Profitez de 200% de bonus pour parier sur ces matchs : Tapez /affiliation*"

user_states = {}
user_last_request_time = {}
ANTI_SPAM_DELAY = 3.0
DAILY_LIMIT_FREE = 5

# Catalogues de données (Affiliation avec 1xBet & 8 Ebooks)
AFFILIATES = {
    "1xbet": {
        "name": "1xBet — Paris Sportifs",
        "link": "https://reffpa.com/L?tag=d_5087549m_1573c_whatsapp&site=5087549&ad=1573",
        "promo": "HILAIREBET",
        "desc": (
            "🔴 **1XBET — PARIS SPORTIFS**\n\n"
            "Profitez de jusqu'à **200% de bonus** sur votre premier dépôt !\n\n"
            "👉 **Lien d'inscription :** [Cliquez ici pour vous inscrire](https://reffpa.com/L?tag=d_5087549m_1573c_whatsapp&site=5087549&ad=1573)\n"
            "🔑 **Code Promo Exclusif :** `HILAIREBET`"
        ),
    },
    "melbet": {
        "name": "MelBet — Paris Sportifs",
        "link": "https://refpa3665.com/L?tag=d_5997062m_53523c_whatsapp&site=5997062&ad=53523",
        "promo": "HILAIREBET",
        "desc": (
            "🔵 **MELBET — PARIS SPORTIFS**\n\n"
            "Profitez de jusqu'à **200% de bonus** sur votre premier dépôt !\n\n"
            "👉 **Lien d'inscription :** [Cliquez ici pour vous inscrire](https://refpa3665.com/L?tag=d_5997062m_53523c_whatsapp&site=5997062&ad=53523)\n"
            "🔑 **Code Promo Exclusif :** `HILAIREBET`"
        ),
    },
    "exness": {
        "name": "Exness — Forex & Trading",
        "link": "https://one.exnessonelink.com/a/395vyusacl",
        "promo": "395vyusacl",
        "desc": (
            "🟡 **EXNESS — FOREX & TRADING**\n\n"
            "Plateforme de trading ultra-rapide avec spreads réduits.\n\n"
            "👉 **Lien d'inscription :** [Cliquez ici pour vous inscrire](https://one.exnessonelink.com/a/395vyusacl)\n"
            "🔑 **Code Partenaire :** `395vyusacl`"
        ),
    },
    "kucoin": {
        "name": "KuCoin — Crypto Exchange",
        "link": "https://www.kucoin.com/ucenter/signup?&rcode=rEN8V1E&utm_medium=U17710",
        "promo": "rEN8V1E",
        "desc": (
            "🟢 **KUCOIN — CRYPTO EXCHANGE**\n\n"
            "Bénéficiez de réductions exclusives sur vos frais de trading crypto.\n\n"
            "👉 **Lien d'inscription :** [Cliquez ici pour vous inscrire](https://www.kucoin.com/ucenter/signup?&rcode=rEN8V1E&utm_medium=U17710)\n"
            "🔑 **Code Parrain :** `rEN8V1E`"
        ),
    },
}

LEAGUES = {
    "CL": {"id": "2001", "name": "UEFA Champions League"},
    "PD": {"id": "2014", "name": "La Liga (Espagne)"},
    "FL1": {"id": "2015", "name": "Ligue 1 (France)"},
    "PL": {"id": "2021", "name": "Premier League (Angleterre)"},
    "BL1": {"id": "2002", "name": "Bundesliga (Allemagne)"},
    "SA": {"id": "2019", "name": "Serie A (Italie)"},
    "WC": {"id": "2000", "name": "Coupe du Monde / CAN"},
}

EBOOKS = {
    "mkt": {
        "title": "Formation en Marketing Digital",
        "file_name": "marketing1.pdf",
        "stars": 150,
        "price_usd": "3$",
        "summary": "📈 **Guide Marketing Digital**\nApprenez à maîtriser la publicité en ligne et l'acquisition de clients.",
    },
    "web": {
        "title": "Création des Sites Web",
        "file_name": "web1.pdf",
        "stars": 150,
        "price_usd": "3$",
        "summary": "💻 **Guide Création de Sites Web**\nConcevez et déployez des sites internet professionnels étape par étape.",
    },
    "med": {
        "title": "Méditation Guide Complet",
        "file_name": "meditation1.pdf",
        "stars": 250,
        "price_usd": "5$",
        "summary": "🧘 **Guide de Méditation**\nUn manuel pratique pour maîtriser le stress et améliorer votre concentration.",
    },
    "tr1": {
        "title": "Débuter en Trading",
        "file_name": "trading1.pdf",
        "stars": 200,
        "price_usd": "4$",
        "summary": "📊 **Débuter en Trading**\nLes fondamentaux de l'analyse technique et de la gestion du risque pour débutants.",
    },
    "bot1": {
        "title": "Création des Bots Telegram",
        "file_name": "bot1.pdf",
        "stars": 250,
        "price_usd": "5$",
        "summary": "🤖 **Création de Bots Telegram**\nDéveloppez et automatisez vos propres bots Telegram de A à Z.",
    },
    "enl1": {
        "title": "Gagner de l'Argent en Ligne",
        "file_name": "enligne1.pdf",
        "stars": 200,
        "price_usd": "4$",
        "summary": "💡 **Gagner de l'Argent en Ligne**\nDécouvrez les meilleures stratégies éprouvées d'opportunités digitales.",
    },
    "aura1": {
        "title": "Comment Retrouver son Aura",
        "file_name": "aura1.pdf",
        "stars": 150,
        "price_usd": "3$",
        "summary": "✨ **Retrouver son Aura**\nUn guide de développement personnel pour renforcer sa présence et sa confiance.",
    },
    "stoi1": {
        "title": "Devenir Stoïque",
        "file_name": "stoique1.pdf",
        "stars": 250,
        "price_usd": "5$",
        "summary": "🏛️ **Devenir Stoïque**\nApprenez à développer une résilience mentale inébranlable au quotidien.",
    },
}

# ---------------------------------------------------------
# 2. SERVEUR WEB (KEEP ALIVE RENDER)
# ---------------------------------------------------------
app = Flask("")


@app.route("/")
def home():
    return "Bot Telegram est EN LIGNE !", 200


def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()


# ---------------------------------------------------------
# 3. BASE DE DONNÉES SQLITE & AUDIT PAIEMENTS
# ---------------------------------------------------------
DB_FILE = "bot_database.db"


def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db_connection() as conn:
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                payment_id TEXT PRIMARY KEY,
                user_id INTEGER,
                amount INTEGER,
                currency TEXT,
                product_payload TEXT,
                status TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
    logger.info("Base de données initialisée avec succès.")


init_db()


# ---------------------------------------------------------
# 4. GESTION ATOMIQUE DU COMPTEUR FREE
# ---------------------------------------------------------
def check_user_status(user_id: int) -> Tuple[bool, str, int]:
    if user_id == ADMIN_ID:
        return True, "ADMIN", 0

    today_str = datetime.now().strftime("%Y-%m-%d")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, daily_requests, last_request_date, vip_expiry FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = cursor.fetchone()

        if not row:
            cursor.execute(
                "INSERT INTO users (user_id, status, daily_requests, last_request_date) VALUES (?, 'FREE', 0, ?)",
                (user_id, today_str),
            )
            conn.commit()
            return True, "FREE", 0

        status, count, last_date, vip_expiry = (
            row["status"],
            row["daily_requests"],
            row["last_request_date"],
            row["vip_expiry"],
        )

        if status in ["PREMIUM", "VIP"]:
            if vip_expiry:
                try:
                    expiry_dt = datetime.strptime(vip_expiry, "%Y-%m-%d %H:%M:%S")
                    if datetime.now() > expiry_dt:
                        cursor.execute(
                            "UPDATE users SET status = 'FREE', vip_expiry = NULL WHERE user_id = ?",
                            (user_id,),
                        )
                        conn.commit()
                        status = "FREE"
                    else:
                        return True, "VIP Illimité", 0
                except ValueError:
                    return True, "VIP Illimité", 0
            else:
                return True, "VIP Illimité", 0

        if last_date != today_str:
            cursor.execute(
                "UPDATE users SET daily_requests = 0, last_request_date = ? WHERE user_id = ?",
                (today_str, user_id),
            )
            conn.commit()
            count = 0

        if count >= DAILY_LIMIT_FREE:
            return False, "FREE", count

        return True, "FREE", count


def check_and_consume_request_atomic(user_id: int) -> Tuple[bool, str]:
    if user_id == ADMIN_ID:
        return True, "ADMIN"

    today_str = datetime.now().strftime("%Y-%m-%d")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        cursor.execute(
            "SELECT status, daily_requests, last_request_date, vip_expiry FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = cursor.fetchone()

        if not row:
            cursor.execute(
                "INSERT INTO users (user_id, status, daily_requests, last_request_date) VALUES (?, 'FREE', 1, ?)",
                (user_id, today_str),
            )
            conn.commit()
            return True, f"FREE (1/{DAILY_LIMIT_FREE})"

        status, count, last_date, vip_expiry = (
            row["status"],
            row["daily_requests"],
            row["last_request_date"],
            row["vip_expiry"],
        )

        if status in ["PREMIUM", "VIP"]:
            if vip_expiry:
                try:
                    expiry_dt = datetime.strptime(vip_expiry, "%Y-%m-%d %H:%M:%S")
                    if datetime.now() > expiry_dt:
                        status = "FREE"
                        cursor.execute(
                            "UPDATE users SET status = 'FREE', vip_expiry = NULL WHERE user_id = ?",
                            (user_id,),
                        )
                    else:
                        conn.commit()
                        return True, "VIP Illimité"
                except ValueError:
                    conn.commit()
                    return True, "VIP Illimité"
            else:
                conn.commit()
                return True, "VIP Illimité"

        if last_date != today_str:
            cursor.execute(
                "UPDATE users SET daily_requests = 1, last_request_date = ? WHERE user_id = ?",
                (today_str, user_id),
            )
            conn.commit()
            return True, f"FREE (1/{DAILY_LIMIT_FREE})"

        if count >= DAILY_LIMIT_FREE:
            conn.commit()
            return False, f"FREE ({count}/{DAILY_LIMIT_FREE})"

        new_count = count + 1
        cursor.execute(
            "UPDATE users SET daily_requests = ? WHERE user_id = ?",
            (new_count, user_id),
        )
        conn.commit()
        return True, f"FREE ({new_count}/{DAILY_LIMIT_FREE})"


# ---------------------------------------------------------
# 5. SÉCURITÉ ET ANTI-SPAM
# ---------------------------------------------------------
def check_rate_limit(user_id: int) -> bool:
    now = time.time()
    last_time = user_last_request_time.get(user_id, 0)
    if now - last_time < ANTI_SPAM_DELAY:
        return False
    user_last_request_time[user_id] = now
    return True


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def sanitize_symbol(symbol: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]", "", symbol.upper())
    return cleaned if 2 <= len(cleaned) <= 12 else None


# ---------------------------------------------------------
# 6. ENGINS DE TRAITEMENT (Crypto Approfondi, Backtest, Football par Ligue)
# ---------------------------------------------------------
def requete_binance_securisee(url_path):
    domaines = [
        "https://api1.binance.com",
        "https://api2.binance.com",
        "https://api3.binance.com",
        "https://data-api.binance.vision",
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
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
        return False, "❌ Symbole invalide. Exemple valide : `BTCUSDT`.", None

    url_path = f"/api/v3/klines?symbol={pair_clean}&interval=15m&limit=100"
    data = requete_binance_securisee(url_path)

    if not data:
        return (
            False,
            f"❌ Paire `{pair_clean}` introuvable ou indisponible actuellement.",
            None,
        )

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

        if score >= 1:
            direction = "LONG / ACHAT"
            entry_low = price * 0.998
            entry_high = price * 1.001
            tp1 = price * 1.012
            tp2 = price * 1.025
            sl = price * 0.988
            confiance = min(70 + abs(score) * 6, 95)
            risq_str = "Faible / Moyen" if score >= 3 else "Moyen"
        elif score <= -1:
            direction = "SHORT / VENTE"
            entry_low = price * 0.999
            entry_high = price * 1.002
            tp1 = price * 0.988
            tp2 = price * 0.975
            sl = price * 1.012
            confiance = min(70 + abs(score) * 6, 95)
            risq_str = "Faible / Moyen" if score <= -3 else "Moyen"
        else:
            direction = "NEUTRE"
            entry_low, entry_high, tp1, tp2, sl = price, price, price, price, price
            confiance = 50
            risq_str = "Élevé"

        res = (
            f"📈 **SIGNAL {pair_clean}**\n\n"
            f"🟢 **Direction :** `{direction}`\n"
            f"⏱️ **Timeframe :** `15 MIN`\n"
            f"🎯 **Entrée :** `{entry_low:,.2f} $ - {entry_high:,.2f} $`\n"
            f"🥇 **TP1 :** `{tp1:,.2f} $`\n"
            f"🥈 **TP2 :** `{tp2:,.2f} $`\n"
            f"🛑 **Stop Loss :** `{sl:,.2f} $`\n"
            f"⚠️ **Risque :** `{risq_str}`\n"
            f"📊 **Confiance du signal :** `{confiance}%`"
            f"{CROSS_SELL_CRYPTO}"
            f"{WARN}"
        )

        copy_text = f"{pair_clean} {direction.split()[0]} Entry: {entry_low:.2f} TP: {tp1:.2f} SL: {sl:.2f}"
        return True, res, copy_text

    except Exception as e:
        logger.error(f"Erreur analyse crypto : {e}")
        return False, "❌ Erreur de calcul des indicateurs.", None


def executer_backtest(pair):
    pair_clean = sanitize_symbol(pair)
    if not pair_clean:
        return False, "❌ Symbole invalide pour le backtest."

    url_path = f"/api/v3/klines?symbol={pair_clean}&interval=1h&limit=500"
    data = requete_binance_securisee(url_path)

    if not data:
        return False, f"❌ Paire `{pair_clean}` indisponible pour le backtest."

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
        res = (
            f"📉 **BACKTEST STRATÉGIE (500 Bougies 1H)**\n\n"
            f"🪙 **Paire :** `{pair_clean}`\n"
            f"🔢 **Signaux exécutés :** `{total}`\n"
            f"✅ **Trades gagnants :** `{wins}`\n"
            f"📊 **Taux de réussite :** `{winrate:.1f}%`"
            f"{CROSS_SELL_CRYPTO}"
            f"{WARN}"
        )
        return True, res
    except Exception as e:
        logger.error(f"Erreur backtest : {e}")
        return False, "❌ Erreur de calcul lors du backtest."


def obtenir_analyses_matchs_par_ligue(league_code):
    league_info = LEAGUES.get(league_code)
    if not league_info:
        return False, "❌ Compétition invalide."

    if not FOOTBALL_KEY:
        return False, "⚽ Clé API Football non configurée."

    league_id = league_info["id"]
    url = f"https://api.football-data.org/v4/competitions/{league_id}/matches?status=SCHEDULED"
    headers = {"X-Auth-Token": FOOTBALL_KEY}

    try:
        response = requests.get(url, headers=headers, timeout=6)
        if response.status_code != 200:
            return (
                False,
                f"⚽ Aucun match de {league_info['name']} n'est prévu pour le moment. Revenez bientôt !",
            )

        data = response.json()
        matches = data.get("matches", [])[:5]

        if not matches:
            return (
                False,
                f"⚽ Aucun match de {league_info['name']} n'est disponible aujourd'hui. Revenez très bientôt !",
            )

        message = (
            f"⚽ **PRONOSTICS — {league_info['name'].upper()}** ⚽\n\n"
        )
        for idx, match in enumerate(matches, 1):
            equipe_dom = match["homeTeam"]["name"]
            equipe_ext = match["awayTeam"]["name"]
            utc_date = match["utcDate"][:10]

            message += f"**{idx}. {equipe_dom} vs {equipe_ext}** ({utc_date})\n"
            message += f"📊 *Analyse :* Match disputé entre équipes clés.\n"
            message += (
                f"💡 *Pronostic :* Plus de 1.5 buts / Victoire ou Nul {equipe_dom}\n\n"
            )

        message += f"{CROSS_SELL_FOOT}"
        return True, message
    except Exception as e:
        logger.error(f"Erreur Football API : {e}")
        return False, f"⚽ Aucun match de {league_info['name']} n'est prévu aujourd'hui. Revenez très bientôt !"


# ---------------------------------------------------------
# 7. WORKER DE NOTIFICATION AUTOMATIQUE (BTC & ETH)
# ---------------------------------------------------------
def background_signal_notifier():
    while True:
        try:
            time.sleep(900)
            for pair in ["BTCUSDT", "ETHUSDT"]:
                success, text, copy_text = analyser_crypto(pair)
                if success and ("90%" in text or "95%" in text):
                    with get_db_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT user_id FROM users")
                        users = cursor.fetchall()

                    for user in users:
                        try:
                            markup = InlineKeyboardMarkup()
                            if copy_text:
                                markup.add(
                                    InlineKeyboardButton(
                                        "📋 Copier le signal",
                                        callback_data=f"cp_{pair}",
                                    )
                                )
                            bot.send_message(
                                user["user_id"],
                                f"🚨 **ALERTE SIGNAL FORT EN TEMPS RÉEL**\n\n{text}",
                                reply_markup=markup,
                            )
                        except Exception:
                            continue
        except Exception as e:
            logger.error(f"Erreur Worker Notification : {e}")


# ---------------------------------------------------------
# 8. COMMANDES LIBRES
# ---------------------------------------------------------
@bot.message_handler(commands=["start"])
def send_welcome(msg):
    user_states[msg.chat.id] = None
    text = (
        "👋 **Bienvenue sur TRADING & PRONOSTICS BOT !**\n\n"
        "👑 **Membre Gratuit :** 5 requêtes offertes par jour.\n"
        "⭐ **Pass VIP (30 Jours) :** Accès illimité 24/7 ! Tapez `/vip`.\n"
        "🤝 **Partenaires :** Offres exclusives via `/affiliation`.\n"
        "📚 **Formations :** Découvrez nos 8 Ebooks via `/ebooks`."
    )
    bot.reply_to(msg, text)


@bot.message_handler(commands=["help", "cmds"])
def send_help(msg):
    user_states[msg.chat.id] = None
    text = (
        "🤖 **MegaBot Trading & Sport v4.0**\n\n"
        "📈 `/crypto` : Signaux trading détaillés (TP, SL, Risque)\n"
        "📉 `/backtest` : Simulation stratégie 500 bougies\n"
        "⚽ `/pari` : Pronostics football par compétitions\n"
        "⭐ `/vip` : Pass VIP 30 jours illimités\n"
        "🤝 `/affiliation` : Codes promo et liens partenaires\n"
        "📚 `/ebooks` : 8 Formations PDF disponibles\n"
        f"{WARN}"
    )
    bot.reply_to(msg, text)


@bot.message_handler(commands=["vip", "premium", "buy"])
def command_vip(msg):
    user_states[msg.chat.id] = None
    markup = InlineKeyboardMarkup()
    btn_stars = InlineKeyboardButton(
        "⭐ S'abonner VIP 30 Jours (250 Stars)", callback_data="buy_vip_stars"
    )
    markup.add(btn_stars)

    txt = (
        "👑 **PASS VIP 30 JOURS - TELEGRAM STARS**\n\n"
        "Débloquez l'accès total pendant **30 jours** :\n"
        "✅ **Analyses Crypto & Signaux ILLIMITÉS**\n"
        "✅ **Backtests ILLIMITÉS**\n"
        "✅ **Pronostics Football par Ligues**\n\n"
        "Prix : **250 Telegram Stars / mois**\n"
        "Cliquez ci-dessous pour lancer le paiement sécurisé :"
    )
    bot.reply_to(msg, txt, reply_markup=markup)


@bot.message_handler(commands=["affiliation", "partenaires", "partenaire"])
def command_affiliation(msg):
    user_states[msg.chat.id] = None
    markup = InlineKeyboardMarkup()
    for key, data in AFFILIATES.items():
        markup.add(
            InlineKeyboardButton(
                f"🎁 {data['name']}", callback_data=f"aff_{key}"
            )
        )

    bot.reply_to(
        msg,
        "🤝 **PARTENAIRES & CODES PROMO EXCLUSIFS**\n\n"
        "Sélectionnez une plateforme partenaire ci-dessous pour obtenir votre lien et code promo :",
        reply_markup=markup,
    )


@bot.message_handler(commands=["ebooks", "ebook", "formations", "formation"])
def command_ebooks(msg):
    user_states[msg.chat.id] = None
    markup = InlineKeyboardMarkup(row_width=1)
    for key, data in EBOOKS.items():
        btn_text = f"📘 {data['title']} ({data['price_usd']})"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"eb_view_{key}"))

    bot.reply_to(
        msg,
        "📚 **CATALOGUE DE FORMATIONS & EBOOKS (8 PDF)**\n\n"
        "Cliquez sur une formation ci-dessous pour lire le résumé et commander :",
        reply_markup=markup,
    )


@bot.message_handler(commands=["admin"])
def cmd_admin(msg):
    user_states[msg.chat.id] = None
    if not is_admin(msg.from_user.id):
        bot.reply_to(msg, "⛔ **Accès refusé.** Réservé à l'administrateur.")
        return

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*), SUM(CASE WHEN status IN ('PREMIUM', 'VIP') THEN 1 ELSE 0 END) FROM users"
        )
        row = cursor.fetchone()
        total_users, premium_users = row[0], row[1] or 0

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

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET status = 'PREMIUM', vip_expiry = ? WHERE user_id = ?",
            (expiry_date, target_id),
        )
        conn.commit()

    bot.reply_to(
        msg,
        f"✅ L'utilisateur `{target_id}` est **PREMIUM pour 30 jours** (Jusqu'au {expiry_date}) !",
    )


# ---------------------------------------------------------
# 9. COMMANDES PROTÉGÉES (PARIS SPORTIFS PAR LIGUE & CRYPTO)
# ---------------------------------------------------------
@bot.message_handler(commands=["pari", "paris"])
def handle_paris_menu(msg):
    user_states[msg.chat.id] = None
    if not check_rate_limit(msg.from_user.id):
        return bot.reply_to(msg, "⏳ *Anti-Spam :* Patientez 3 secondes.")

    allowed, status_str, count = check_user_status(msg.from_user.id)
    if not allowed:
        return bot.reply_to(
            msg,
            "❌ **Quota Quotidien Atteint (5/5) !**\n\nVous avez consommé vos 5 requêtes gratuites du jour.\n👉 Tapez `/vip` pour vous abonner et débloquer l'accès **ILLIMITÉ** !",
        )

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🏆 UEFA Champions League", callback_data="lg_CL"),
        InlineKeyboardButton("🇪🇸 La Liga (Espagne)", callback_data="lg_PD"),
        InlineKeyboardButton("🇫🇷 Ligue 1 (France)", callback_data="lg_FL1"),
        InlineKeyboardButton("🇬🇧 Premier League (Angleterre)", callback_data="lg_PL"),
        InlineKeyboardButton("🇩🇪 Bundesliga (Allemagne)", callback_data="lg_BL1"),
        InlineKeyboardButton("🇮🇹 Serie A (Italie)", callback_data="lg_SA"),
        InlineKeyboardButton("🌍 CAN / Coupe du Monde", callback_data="lg_WC"),
    )
    bot.reply_to(
        msg,
        "⚽ **PRONOSTICS SPORTIFS — CHOISISSEZ UNE COMPÉTITION**\n\nSélectionnez une ligue pour consulter tous les matchs prévus :",
        reply_markup=markup,
    )


@bot.message_handler(commands=["crypto"])
def command_crypto(msg):
    user_states[msg.chat.id] = None
    if not check_rate_limit(msg.from_user.id):
        return bot.reply_to(msg, "⏳ *Anti-Spam :* Patientez 3 secondes.")

    allowed, status_str, count = check_user_status(msg.from_user.id)
    if not allowed:
        return bot.reply_to(
            msg,
            "❌ **Quota Quotidien Atteint (5/5) !**\n\nVous avez consommé vos 5 requêtes gratuites du jour.\n👉 Tapez `/vip` pour vous abonner et débloquer l'accès **ILLIMITÉ** !",
        )

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🟡 BTC/USDT", callback_data="c_BTCUSDT"),
        InlineKeyboardButton("🔹 ETH/USDT", callback_data="c_ETHUSDT"),
        InlineKeyboardButton("☀️ SOL/USDT", callback_data="c_SOLUSDT"),
        InlineKeyboardButton("🔶 BNB/USDT", callback_data="c_BNBUSDT"),
        InlineKeyboardButton("🌐 XRP/USDT", callback_data="c_XRPUSDT"),
        InlineKeyboardButton("✍️ Autre symbole...", callback_data="c_custom"),
    )
    bot.reply_to(
        msg,
        "📈 **ANALYSE & SIGNAUX TRADING CRYPTO**\n\nSélectionnez une paire :",
        reply_markup=markup,
    )


@bot.message_handler(commands=["backtest"])
def command_backtest(msg):
    user_states[msg.chat.id] = None
    if not check_rate_limit(msg.from_user.id):
        return bot.reply_to(msg, "⏳ *Anti-Spam :* Patientez 3 secondes.")

    allowed, status_str, count = check_user_status(msg.from_user.id)
    if not allowed:
        return bot.reply_to(
            msg,
            "❌ **Quota Quotidien Atteint (5/5) !**\n\nVous avez consommé vos 5 requêtes gratuites du jour.\n👉 Tapez `/vip` pour vous abonner et débloquer l'accès **ILLIMITÉ** !",
        )

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🟡 BTC/USDT", callback_data="bt_BTCUSDT"),
        InlineKeyboardButton("🔹 ETH/USDT", callback_data="bt_ETHUSDT"),
        InlineKeyboardButton("☀️ SOL/USDT", callback_data="bt_SOLUSDT"),
        InlineKeyboardButton("✍️ Autre symbole...", callback_data="bt_custom"),
    )
    bot.reply_to(
        msg,
        "📉 **BACKTEST STRATÉGIE**\n\nSélectionnez une paire :",
        reply_markup=markup,
    )


# ---------------------------------------------------------
# 10. CALLBACKS & GESTION DES LIGUES ET DU COPY TRADING
# ---------------------------------------------------------
@bot.callback_query_handler(func=lambda call: call.data.startswith("lg_"))
def handle_league_callbacks(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    if not check_rate_limit(user_id):
        return bot.answer_callback_query(
            call.id, "⏳ Patientez 3 secondes...", show_alert=True
        )

    allowed, status_str, count = check_user_status(user_id)
    if not allowed:
        bot.send_message(
            chat_id,
            "❌ **Quota Quotidien Atteint (5/5) !**\n\nVous avez consommé vos 5 requêtes gratuites du jour.\n👉 Tapez `/vip` pour vous abonner et débloquer l'accès **ILLIMITÉ** !",
        )
        bot.answer_callback_query(call.id)
        return

    league_code = call.data.replace("lg_", "")
    bot.answer_callback_query(call.id, "Recherche des matchs...")

    success, res_text = obtenir_analyses_matchs_par_ligue(league_code)

    if success:
        consumed, quota_info = check_and_consume_request_atomic(user_id)
        bot.send_message(chat_id, f"{res_text}\n\n📊 *Consommation :* `{quota_info}`")
    else:
        bot.send_message(chat_id, res_text)


@bot.callback_query_handler(func=lambda call: call.data.startswith("c_"))
def handle_crypto_callbacks(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    if not check_rate_limit(user_id):
        return bot.answer_callback_query(
            call.id, "⏳ Patientez 3 secondes...", show_alert=True
        )

    pair = call.data.replace("c_", "")
    if pair == "custom":
        user_states[chat_id] = "WAITING_CRYPTO_PAIR"
        bot.send_message(
            chat_id, "🔍 Tapez le nom de la paire à analyser (ex: `ADAUSDT`) :"
        )
        bot.answer_callback_query(call.id)
        return

    allowed, status_str, count = check_user_status(user_id)
    if not allowed:
        bot.send_message(
            chat_id,
            "❌ **Quota Quotidien Atteint (5/5) !**\n\nVous avez consommé vos 5 requêtes gratuites du jour.\n👉 Tapez `/vip` pour vous abonner et débloquer l'accès **ILLIMITÉ** !",
        )
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id, "Analyse en cours...")
    success, res_text, copy_text = analyser_crypto(pair)

    if success:
        consumed, quota_info = check_and_consume_request_atomic(user_id)
        markup = InlineKeyboardMarkup()
        if copy_text:
            markup.add(
                InlineKeyboardButton(
                    "📋 Copier le signal", callback_data=f"cp_{pair}"
                )
            )

        bot.send_message(
            chat_id,
            f"{res_text}\n\n📊 *Consommation :* `{quota_info}`",
            reply_markup=markup,
        )
    else:
        bot.send_message(chat_id, res_text)


@bot.callback_query_handler(func=lambda call: call.data.startswith("cp_"))
def handle_copy_signal(call):
    pair = call.data.replace("cp_", "")
    success, res_text, copy_text = analyser_crypto(pair)
    if success and copy_text:
        bot.answer_callback_query(call.id, "Signal copié !", show_alert=False)
        bot.send_message(
            call.message.chat.id,
            f"📋 **SIGNAL PRÊT À COLLER :**\n\n`{copy_text}`\n\nCopiez et collez ce texte dans Exness ou KuCoin.",
        )
    else:
        bot.answer_callback_query(
            call.id, "Impossible de copier le signal.", show_alert=True
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("bt_"))
def handle_backtest_callbacks(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    if not check_rate_limit(user_id):
        return bot.answer_callback_query(
            call.id, "⏳ Patientez 3 secondes...", show_alert=True
        )

    pair = call.data.replace("bt_", "")
    if pair == "custom":
        user_states[chat_id] = "WAITING_BACKTEST_PAIR"
        bot.send_message(
            chat_id, "🔍 Tapez le nom de la paire pour le backtest (ex: `DOGEUSDT`) :"
        )
        bot.answer_callback_query(call.id)
        return

    allowed, status_str, count = check_user_status(user_id)
    if not allowed:
        bot.send_message(
            chat_id,
            "❌ **Quota Quotidien Atteint (5/5) !**\n\nVous avez consommé vos 5 requêtes gratuites du jour.\n👉 Tapez `/vip` pour vous abonner et débloquer l'accès **ILLIMITÉ** !",
        )
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id, "Backtest en cours...")
    success, res_text = executer_backtest(pair)

    if success:
        consumed, quota_info = check_and_consume_request_atomic(user_id)
        bot.send_message(chat_id, f"{res_text}\n\n📊 *Consommation :* `{quota_info}`")
    else:
        bot.send_message(chat_id, res_text)


@bot.callback_query_handler(func=lambda call: call.data.startswith("aff_"))
def handle_aff_callbacks(call):
    if not check_rate_limit(call.from_user.id):
        return bot.answer_callback_query(
            call.id, "⏳ Patientez 3 secondes...", show_alert=True
        )

    aff_key = call.data.replace("aff_", "")
    partner = AFFILIATES.get(aff_key)
    if partner:
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id, partner["desc"], disable_web_page_preview=False
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("eb_view_"))
def handle_ebook_view_callbacks(call):
    if not check_rate_limit(call.from_user.id):
        return bot.answer_callback_query(
            call.id, "⏳ Patientez 3 secondes...", show_alert=True
        )

    ebook_key = call.data.replace("eb_view_", "")
    ebook = EBOOKS.get(ebook_key)
    if ebook:
        bot.answer_callback_query(call.id)
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(
                f"⭐ Acheter ({ebook['stars']} Stars)",
                callback_data=f"buy_eb_{ebook_key}",
            )
        )

        txt = (
            f"{ebook['summary']}\n\n"
            f"🏷️ **Prix :** `{ebook['price_usd']}` ({ebook['stars']} Stars Telegram)\n"
            f"📥 **Format :** Fichier PDF téléchargeable instantanément."
        )
        bot.send_message(call.message.chat.id, txt, reply_markup=markup)


# ---------------------------------------------------------
# 11. DÉLIVRANCE & SÉCURISATION PAIEMENTS TELEGRAM STARS
# ---------------------------------------------------------
@bot.callback_query_handler(func=lambda call: call.data == "buy_vip_stars")
def process_buy_vip_stars(call):
    payload_unique = f"vip_sub_{call.from_user.id}_{int(time.time())}"
    bot.send_invoice(
        call.message.chat.id,
        title="Pass VIP 30 Jours",
        description="Accès illimité aux signaux Trading Crypto, Backtests et Pronostics Sportifs pendant 30 jours.",
        invoice_payload=payload_unique,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("Abonnement 1 Mois", 250)],
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_eb_"))
def process_buy_ebook_stars(call):
    ebook_key = call.data.replace("buy_eb_", "")
    ebook = EBOOKS.get(ebook_key)
    if not ebook:
        return

    payload_unique = f"eb_pay_{ebook_key}_{call.from_user.id}_{int(time.time())}"
    bot.send_invoice(
        call.message.chat.id,
        title=ebook["title"],
        description=f"Téléchargement immédiat du PDF : {ebook['title']}",
        invoice_payload=payload_unique,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(f"Ebook {ebook['title']}", ebook["stars"])],
    )


@bot.pre_checkout_query_handler(func=lambda query: True)
def process_pre_checkout(query: PreCheckoutQuery):
    payload = query.invoice_payload
    currency = query.currency
    total_amount = query.total_amount

    if currency != "XTR":
        bot.answer_pre_checkout_query(
            query.id, ok=False, error_message="Devise de paiement non supportée."
        )
        return

    if payload.startswith("vip_sub_"):
        if total_amount != 250:
            bot.answer_pre_checkout_query(
                query.id,
                ok=False,
                error_message="Montant du paiement incorrect pour l'offre VIP.",
            )
            return
        bot.answer_pre_checkout_query(query.id, ok=True)
        return

    elif payload.startswith("eb_pay_"):
        parts = payload.split("_")
        if len(parts) >= 3:
            ebook_key = parts[2]
            ebook = EBOOKS.get(ebook_key)
            if ebook and total_amount == ebook["stars"]:
                bot.answer_pre_checkout_query(query.id, ok=True)
                return

    bot.answer_pre_checkout_query(
        query.id, ok=False, error_message="La commande n'est plus valide ou a expiré."
    )


@bot.message_handler(content_types=["successful_payment"])
def process_successful_payment(msg):
    payment_info = msg.successful_payment
    payload = payment_info.invoice_payload
    user_id = msg.from_user.id
    payment_id = (
        payment_info.telegram_payment_charge_id
        or payment_info.provider_payment_charge_id
    )
    amount = payment_info.total_amount
    currency = payment_info.currency
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT payment_id FROM payments WHERE payment_id = ?", (payment_id,)
        )
        if cursor.fetchone():
            bot.reply_to(msg, "⚠️ Ce paiement a déjà été validé et traité.")
            return

        if payload.startswith("vip_sub_"):
            if currency == "XTR" and amount == 250:
                expiry_date = (datetime.now() + timedelta(days=30)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

                cursor.execute(
                    "INSERT INTO payments (payment_id, user_id, amount, currency, product_payload, status, created_at) VALUES (?, ?, ?, ?, ?, 'COMPLETED', ?)",
                    (payment_id, user_id, amount, currency, payload, now_str),
                )
                cursor.execute(
                    "UPDATE users SET status = 'PREMIUM', vip_expiry = ? WHERE user_id = ?",
                    (expiry_date, user_id),
                )
                conn.commit()

                bot.reply_to(
                    msg,
                    f"🎉 **FÉLICITATIONS ET BIENVENUE VIP !** 🎉\n\n"
                    f"Votre abonnement de **30 Jours** a été activé avec succès !\n"
                    f"📅 Valable jusqu'au : `{expiry_date}`",
                )

        elif payload.startswith("eb_pay_"):
            parts = payload.split("_")
            if len(parts) >= 3:
                ebook_key = parts[2]
                ebook = EBOOKS.get(ebook_key)
                if ebook and currency == "XTR" and amount == ebook["stars"]:
                    cursor.execute(
                        "INSERT INTO payments (payment_id, user_id, amount, currency, product_payload, status, created_at) VALUES (?, ?, ?, ?, ?, 'COMPLETED', ?)",
                        (payment_id, user_id, amount, currency, payload, now_str),
                    )
                    conn.commit()

                    file_path = os.path.join("ebooks", ebook["file_name"])
                    if os.path.exists(file_path):
                        bot.reply_to(
                            msg,
                            f"✅ **Paiement confirmé pour :** *{ebook['title']}* !\nVoici votre fichier PDF :",
                        )
                        with open(file_path, "rb") as pdf_file:
                            bot.send_document(
                                msg.chat.id,
                                pdf_file,
                                caption=f"📘 **{ebook['title']}**",
                            )
                    else:
                        bot.reply_to(
                            msg,
                            f"✅ **Paiement confirmé !**\n⚠️ Le fichier `{ebook['file_name']}` n'est pas encore présent sur le serveur. Contactez l'administrateur avec votre ID : `{payment_id}`.",
                        )


# ---------------------------------------------------------
# 12. TRAITEMENT DE TEXTE LIBRE ET VALIDATIONS
# ---------------------------------------------------------
@bot.message_handler(func=lambda msg: not msg.text.startswith("/"))
def handle_text_messages(msg):
    chat_id = msg.chat.id
    user_id = msg.from_user.id
    state = user_states.get(chat_id)

    if not state:
        return

    if not check_rate_limit(user_id):
        return bot.reply_to(msg, "⏳ *Anti-Spam :* Veuillez patienter 3 secondes.")

    if state == "WAITING_CRYPTO_PAIR":
        success, res_text, copy_text = analyser_crypto(msg.text.strip())
        if success:
            consumed, quota_info = check_and_consume_request_atomic(user_id)
            user_states[chat_id] = None
            markup = InlineKeyboardMarkup()
            if copy_text:
                markup.add(
                    InlineKeyboardButton(
                        "📋 Copier le signal",
                        callback_data=f"cp_{msg.text.strip().upper()}",
                    )
                )
            bot.send_message(
                chat_id,
                f"{res_text}\n\n📊 *Consommation :* `{quota_info}`",
                reply_markup=markup,
            )
        else:
            bot.send_message(chat_id, res_text)

    elif state == "WAITING_BACKTEST_PAIR":
        success, res_text = executer_backtest(msg.text.strip())
        if success:
            consumed, quota_info = check_and_consume_request_atomic(user_id)
            user_states[chat_id] = None
            bot.send_message(
                chat_id, f"{res_text}\n\n📊 *Consommation :* `{quota_info}`"
            )
        else:
            bot.send_message(chat_id, res_text)


# ---------------------------------------------------------
# 13. DÉMARRAGE DU BOT
# ---------------------------------------------------------
if __name__ == "__main__":
    keep_alive()
    logger.info("Démarrage du bot v4.0...")

    t_notify = Thread(target=background_signal_notifier)
    t_notify.daemon = True
    t_notify.start()

    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception as e:
        logger.warning(f"Suppression du webhook ignorée : {e}")

    bot.infinity_polling(none_stop=True, skip_pending=True)