import json
import os
import time
from datetime import datetime
from threading import Thread, Lock

from flask import Flask
import pandas as pd
import requests
import ta
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup


# ============================================================
# 1. SERVEUR WEB (KEEP ALIVE)
# ============================================================

app = Flask("")


@app.route("/")
def home():
    return "Bot Telegram est EN LIGNE !"


def run_flask():
    port = int(
        os.environ.get("PORT")
        or os.environ.get("KEEP_ALIVE_PORT", "5000")
    )

    app.run(
        host="0.0.0.0",
        port=port
    )


def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()


# ============================================================
# 2. CONFIGURATION TELEGRAM ET API
# ============================================================

TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN", "")
    .strip()
    .replace('"', "")
    .replace("'", "")
)

FOOTBALL_KEY = (
    os.environ.get("FOOTBALL_API_KEY", "").strip()
    or os.environ.get("API_FOOTBALL", "").strip()
)


if not TOKEN:
    raise ValueError(
        "ERREUR: La variable TELEGRAM_BOT_TOKEN est introuvable !"
    )


bot = telebot.TeleBot(
    TOKEN,
    parse_mode="Markdown"
)

WARN = (
    "\n\n⚠️ *Attention :* Gestion de risque obligatoire. "
    "Interdit aux -18 ans."
)


# ============================================================
# ÉTATS DES UTILISATEURS
# ============================================================

user_states = {}


# ============================================================
# 🔐 SÉCURITÉ — PROTECTION ANTI-SPAM
# ============================================================

# Temps minimum entre deux opérations protégées
# effectuées par le même utilisateur.
RATE_LIMIT_SECONDS = 1.5


# Stocke le dernier moment où chaque utilisateur
# a effectué une opération protégée.
last_request_time = {}


# Empêche plusieurs threads de modifier
# le dictionnaire simultanément.
rate_limit_lock = Lock()


def is_rate_limited(user_id):
    """
    Vérifie si un utilisateur envoie des requêtes
    trop rapidement.

    Retourne :
        True  → requête bloquée
        False → requête autorisée
    """

    now = time.monotonic()

    with rate_limit_lock:

        last_time = last_request_time.get(user_id)

        # Première opération connue de l'utilisateur.
        if last_time is None:
            last_request_time[user_id] = now
            return False

        elapsed = now - last_time

        # L'utilisateur a suffisamment attendu.
        if elapsed >= RATE_LIMIT_SECONDS:
            last_request_time[user_id] = now
            return False

        # Requête trop rapide.
        return True


def reject_if_rate_limited(message):
    """
    Bloque proprement une requête si elle arrive
    trop rapidement.
    """

    user_id = message.from_user.id

    if is_rate_limited(user_id):

        bot.reply_to(
            message,
            "⏳ *Doucement !*\n\n"
            "Trop de requêtes en quelques secondes. "
            "Attends un instant avant de réessayer."
        )

        return True

    return False


# ============================================================
# 3. TRADING & INDICATEURS
# ============================================================

def analyser_crypto(pair):

    pair = (
        pair.upper()
        .replace("/", "")
        .replace("-", "")
    )

    url = (
        "https://api.binance.com/api/v3/klines"
        f"?symbol={pair}&interval=1h&limit=100"
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/115.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json"
    }

    try:

        res = requests.get(
            url,
            headers=headers,
            timeout=10
        )

        if res.status_code != 200:

            return (
                f"❌ Paire `{pair}` introuvable ou "
                f"erreur réseau (Code : `{res.status_code}`)."
            )

        data = res.json()

        closes = pd.Series(
            [float(c[4]) for c in data]
        )

        price = closes.iloc[-1]

        rsi = (
            ta.momentum
            .RSIIndicator(closes, window=14)
            .rsi()
            .iloc[-1]
        )

        macd_indicator = ta.trend.MACD(closes)

        macd_diff = (
            macd_indicator
            .macd_diff()
            .iloc[-1]
        )

        ema20 = (
            ta.trend
            .EMAIndicator(closes, window=20)
            .ema_indicator()
            .iloc[-1]
        )

        ema50 = (
            ta.trend
            .EMAIndicator(closes, window=50)
            .ema_indicator()
            .iloc[-1]
        )

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


        trend = (
            "📈 Haussière"
            if price > ema50
            else "📉 Baissière"
        )


        return (
            f"📊 **ANALYSE TEMPS RÉEL : {pair}**\n\n"
            f"💰 **Prix actuel :** `{price:,.4f} $`\n"
            f"📈 **Tendance globale :** {trend}\n"
            f"🔹 **RSI (14) :** `{rsi:.1f}`\n"
            f"🔹 **MACD Hist :** `{macd_diff:.4f}`\n"
            f"🔹 **EMA 20 / 50 :** "
            f"`{ema20:,.2f} / {ema50:,.2f}`\n\n"
            f"🎯 **Signal détecté :** {signal}"
            f"{WARN}"
        )

    except Exception:

        return (
            "❌ Erreur lors de la connexion "
            "au marché Binance."
        )


def executer_backtest(pair):

    pair = (
        pair.upper()
        .replace("/", "")
        .replace("-", "")
    )

    url = (
        "https://api.binance.com/api/v3/klines"
        f"?symbol={pair}&interval=1h&limit=500"
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/115.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json"
    }

    try:

        res = requests.get(
            url,
            headers=headers,
            timeout=10
        )

        if res.status_code != 200:

            return (
                f"❌ Erreur réseau Binance "
                f"(Code : `{res.status_code}`). "
                f"Impossible de récupérer `{pair}`."
            )

        data = res.json()

        closes = pd.Series(
            [float(c[4]) for c in data]
        )

        rsi_series = (
            ta.momentum
            .RSIIndicator(closes)
            .rsi()
        )

        wins = 0
        total = 0

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


        winrate = (
            wins / total * 100
            if total > 0
            else 0
        )


        return (
            f"📈 **BACKTEST STRATÉGIE "
            f"(500 Bougies 1H)**\n\n"
            f"🪙 **Paire :** `{pair}`\n"
            f"🔢 **Signaux exécutés :** `{total}`\n"
            f"✅ **Trades gagnants :** `{wins}`\n"
            f"📊 **Taux de réussite :** "
            f"`{winrate:.1f}%`"
            f"{WARN}"
        )

    except Exception:

        return (
            "❌ Erreur de calcul "
            "lors du backtest."
        )


# ============================================================
# 4. FOOTBALL
# ============================================================

def obtenir_analyses_matchs():

    url = (
        "https://api.football-data.org/v4/matches"
    )

    headers = {
        "X-Auth-Token": FOOTBALL_KEY
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            timeout=10
        )

        data = response.json()

        matches = data.get(
            "matches",
            []
        )[:3]


        if not matches:

            return (
                "⚽ Aucun gros match au "
                "programme aujourd'hui."
            )


        message = (
            "⚽ **ANALYSES DES 3 GROS MATCHS "
            "DU JOUR** ⚽\n\n"
        )


        for idx, match in enumerate(
            matches,
            1
        ):

            equipe_dom = (
                match["homeTeam"]["name"]
            )

            equipe_ext = (
                match["awayTeam"]["name"]
            )

            competition = (
                match["competition"]["name"]
            )


            message += (
                f"**{idx}. {equipe_dom} "
                f"vs {equipe_ext}** "
                f"({competition})\n"
            )

            message += (
                f"📊 *Analyse :* Rencontre "
                f"équilibrée. Avantage à domicile "
                f"pour {equipe_dom}.\n"
            )

            message += (
                f"💡 *Pronostic :* Plus de 1.5 buts / "
                f"Victoire ou Nul {equipe_dom}\n\n"
            )


        return message


    except Exception:

        return (
            "⚠️ Erreur lors de la récupération "
            "des données de l'API Football."
        )


# ============================================================
# 5. COMMANDES DE DIALOGUE
# ============================================================

@bot.message_handler(commands=["start"])
def send_welcome(msg):

    text = (
        "👋 **Bienvenue sur TRADING & "
        "PRONOSTICS BOT !**\n\n"
        "Cliquez sur la boîte de **Menu** "
        "en bas à gauche pour découvrir "
        "toutes les fonctionnalités !"
    )

    bot.reply_to(
        msg,
        text
    )


@bot.message_handler(commands=["help", "cmds"])
def send_help(msg):

    text = (
        "🤖 **MegaBot Trading & Sport v2.3**\n\n"
        "Voici la liste complète des "
        "fonctionnalités disponibles :\n\n"

        "📈 **Trading Crypto**\n"
        "• `/crypto` : Obtenir un signal "
        "en temps réel (menu interactif)\n"
        "• `/backtest` : Tester la stratégie "
        "technique sur 500 bougies\n\n"

        "⚽ **Football**\n"
        "• `/pari` : Analyses et conseils "
        "sur les 3 gros matchs du jour\n\n"

        "💰 **Gestion de Capital**\n"
        "• `/bankroll` : Calculateur de mise "
        "sécurisée (exposition à 2%)\n\n"

        f"{WARN}"
    )

    bot.reply_to(
        msg,
        text
    )


# ============================================================
# /CRYPTO
# ============================================================

@bot.message_handler(commands=["crypto"])
def command_crypto(msg):

    markup = InlineKeyboardMarkup(
        row_width=2
    )

    b1 = InlineKeyboardButton(
        "🟡 BTC/USDT",
        callback_data="c_BTCUSDT"
    )

    b2 = InlineKeyboardButton(
        "🔹 ETH/USDT",
        callback_data="c_ETHUSDT"
    )

    b3 = InlineKeyboardButton(
        "☀️ SOL/USDT",
        callback_data="c_SOLUSDT"
    )

    b4 = InlineKeyboardButton(
        "🔶 BNB/USDT",
        callback_data="c_BNBUSDT"
    )

    b5 = InlineKeyboardButton(
        "🌐 XRP/USDT",
        callback_data="c_XRPUSDT"
    )

    b6 = InlineKeyboardButton(
        "✍️ Autre symbole...",
        callback_data="c_custom"
    )

    markup.add(
        b1,
        b2,
        b3,
        b4,
        b5,
        b6
    )


    bot.reply_to(
        msg,

        "📈 **ANALYSE CRYPTO EN TEMPS RÉEL**\n\n"
        "Sélectionne une paire ci-dessous "
        "ou clique sur *Autre symbole* :",

        reply_markup=markup
    )


# ============================================================
# /BACKTEST
# ============================================================

@bot.message_handler(commands=["backtest"])
def command_backtest(msg):

    markup = InlineKeyboardMarkup(
        row_width=2
    )

    b1 = InlineKeyboardButton(
        "🟡 BTC/USDT",
        callback_data="bt_BTCUSDT"
    )

    b2 = InlineKeyboardButton(
        "🔹 ETH/USDT",
        callback_data="bt_ETHUSDT"
    )

    b3 = InlineKeyboardButton(
        "☀️ SOL/USDT",
        callback_data="bt_SOLUSDT"
    )

    b4 = InlineKeyboardButton(
        "✍️ Autre symbole...",
        callback_data="bt_custom"
    )

    markup.add(
        b1,
        b2,
        b3,
        b4
    )


    bot.reply_to(
        msg,

        "📉 **BACKTEST STRATÉGIE**\n\n"
        "Sélectionne une paire pour lancer "
        "la simulation sur 500 bougies :",

        reply_markup=markup
    )


# ============================================================
# /BANKROLL
# ============================================================

@bot.message_handler(commands=["bankroll"])
def bankroll_start(msg):

    # 🔐 Protection anti-spam
    if reject_if_rate_limited(msg):
        return


    user_states[msg.chat.id] = (
        "WAITING_BANKROLL"
    )


    bot.reply_to(
        msg,

        "💰 **GESTION DE CAPITAL "
        "(Règle des 2%)**\n\n"
        "Entrez le montant de votre capital "
        "total (ex: `100000` ou `5000`) :"
    )


# ============================================================
# CALLBACKS DES BOUTONS
# ============================================================

@bot.callback_query_handler(
    func=lambda call: True
)
def handle_query(call):

    chat_id = call.message.chat.id


    # ========================================================
    # 🔹 CRYPTO
    # ========================================================

    if call.data.startswith("c_"):

        pair = call.data[2:].upper()


        # 🔐 Protection anti-spam
        if is_rate_limited(
            call.from_user.id
        ):

            bot.answer_callback_query(
                call.id,

                "⏳ Attends un instant avant "
                "de lancer une nouvelle analyse.",

                show_alert=True
            )

            return


        bot.answer_callback_query(
            call.id,
            "Analyse du marché en cours..."
        )


        bot.send_chat_action(
            chat_id,
            "typing"
        )


        res = analyser_crypto(pair)


        bot.send_message(
            chat_id,
            res
        )


    # ========================================================
    # 🔹 BACKTEST
    # ========================================================

    elif call.data.startswith("bt_"):

        pair = call.data[3:].upper()


        # 🔐 Protection anti-spam
        if is_rate_limited(
            call.from_user.id
        ):

            bot.answer_callback_query(
                call.id,

                "⏳ Attends un instant avant "
                "de lancer un nouveau backtest.",

                show_alert=True
            )

            return


        bot.answer_callback_query(
            call.id,
            "Backtest en cours..."
        )


        bot.send_chat_action(
            chat_id,
            "typing"
        )


        res = executer_backtest(pair)


        bot.send_message(
            chat_id,
            res
        )


# ============================================================
# /PARI
# ============================================================

@bot.message_handler(
    commands=["pari", "paris"]
)
def handle_paris(msg):

    # 🔐 Protection anti-spam
    if reject_if_rate_limited(msg):
        return


    bot.send_chat_action(
        msg.chat.id,
        "typing"
    )


    analyse = obtenir_analyses_matchs()


    bot.reply_to(
        msg,
        analyse,
        parse_mode="Markdown"
    )


# ============================================================
# ÉCOUTEUR TEXTE
# ============================================================

@bot.message_handler(
    func=lambda msg: (
        msg.text
        and not msg.text.startswith("/")
    )
)
def handle_text_messages(msg):

    chat_id = msg.chat.id

    state = user_states.get(
        chat_id
    )


    # ========================================================
    # BANKROLL
    # ========================================================

    if state == "WAITING_BANKROLL":

        user_states[chat_id] = None

        cleaned_text = (
            msg.text
            .replace(" ", "")
            .replace(",", ".")
            .strip()
        )


        try:

            capital = float(
                cleaned_text
            )

            mise = capital * 0.02


            res = (
                f"💼 **CALCUL DE RISQUE "
                f"(2% STRICT)**\n\n"

                f"💵 **Capital indiqué :** "
                f"`{capital:,.2f}`\n"

                f"📊 **Risque autorisé :** "
                f"`2%`\n"

                f"🎯 **Mise maximale "
                f"recommandée :** "
                f"`{mise:,.2f}`"

                f"{WARN}"
            )


            bot.send_message(
                chat_id,
                res
            )


        except ValueError:

            bot.send_message(
                chat_id,

                "❌ Veuillez entrer un nombre "
                "valide (ex: `100000`). "
                "Tapez `/bankroll` pour recommencer."
            )


    # ========================================================
    # CRYPTO PERSONNALISÉ
    # ========================================================

    elif state == "WAITING_CRYPTO_PAIR":

        user_states[chat_id] = None


        # 🔐 Protection anti-spam
        if is_rate_limited(
            msg.from_user.id
        ):
            bot.send_message(
                chat_id,

                "⏳ Attends un instant "
                "avant de lancer une nouvelle analyse."
            )

            return


        bot.send_chat_action(
            chat_id,
            "typing"
        )


        res = analyser_crypto(
            msg.text.strip()
        )


        bot.send_message(
            chat_id,
            res
        )


    # ========================================================
    # BACKTEST PERSONNALISÉ
    # ========================================================

    elif state == "WAITING_BACKTEST_PAIR":

        user_states[chat_id] = None


        # 🔐 Protection anti-spam
        if is_rate_limited(
            msg.from_user.id
        ):
            bot.send_message(
                chat_id,

                "⏳ Attends un instant "
                "avant de lancer un nouveau backtest."
            )

            return


        bot.send_chat_action(
            chat_id,
            "typing"
        )


        res = executer_backtest(
            msg.text.strip()
        )


        bot.send_message(
            chat_id,
            res
        )


# ============================================================
# 6. DÉMARRAGE DU BOT
# ============================================================

if __name__ == "__main__":

    keep_alive()

    print(
        "🤖 Bot démarré avec succès !"
    )

    bot.infinity_polling(
        none_stop=True
    )