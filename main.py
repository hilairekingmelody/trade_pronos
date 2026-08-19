import json
import os
from datetime import datetime
from threading import Thread

from flask import Flask
import pandas as pd
import requests
import ta
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

# 1. SERVEUR WEB (Keep Alive)
app = Flask("")


@app.route("/")
def home():
    return "Bot Telegram est EN LIGNE !"


def run_flask():
    port = int(os.environ.get("PORT") or os.environ.get("KEEP_ALIVE_PORT", "5000"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()


# 2. CONFIGURATION TELEGRAM ET API
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().replace('"', "").replace("'", "")
FOOTBALL_KEY = os.environ.get("FOOTBALL_API_KEY", "").strip() or os.environ.get("API_FOOTBALL", "").strip()

if not TOKEN:
    raise ValueError("ERREUR: La variable TELEGRAM_BOT_TOKEN est introuvable !")

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
WARN = "\n\n⚠️ *Attention :* Gestion de risque obligatoire. Interdit aux -18 ans."

user_states = {}


# 3. TRADING & INDICATEURS
def analyser_crypto(pair):
    pair = pair.upper().replace("/", "").replace("-", "")
    url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1h&limit=100"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            return f"❌ Paire `{pair}` introuvable ou erreur réseau (Code : `{res.status_code}`)."

        data = res.json()
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
            f"📊 **ANALYSE TEMPS RÉEL : {pair}**\n\n"
            f"💰 **Prix actuel :** `{price:,.4f} $`\n"
            f"📈 **Tendance globale :** {trend}\n"
            f"🔹 **RSI (14) :** `{rsi:.1f}`\n"
            f"🔹 **MACD Hist :** `{macd_diff:.4f}`\n"
            f"🔹 **EMA 20 / 50 :** `{ema20:,.2f} / {ema50:,.2f}`\n\n"
            f"🎯 **Signal détecté :** {signal}"
            f"{WARN}"
        )
    except Exception:
        return "❌ Erreur lors de la connexion au marché Binance."


def executer_backtest(pair):
    pair = pair.upper().replace("/", "").replace("-", "")
    url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1h&limit=500"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            return f"❌ Erreur réseau Binance (Code : `{res.status_code}`). Impossible de récupérer `{pair}`."

        data = res.json()
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
            f"🪙 **Paire :** `{pair}`\n"
            f"🔢 **Signaux exécutés :** `{total}`\n"
            f"✅ **Trades gagnants :** `{wins}`\n"
            f"📊 **Taux de réussite :** `{winrate:.1f}%`"
            f"{WARN}"
        )
    except Exception:
        return "❌ Erreur de calcul lors du backtest."


# 4. FOOTBALL
def obtenir_analyses_matchs():
    url = "https://api.football-data.org/v4/matches"
    headers = {"X-Auth-Token": FOOTBALL_KEY}

    try:
        response = requests.get(url, headers=headers, timeout=10)
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
            message += f"📊 *Analyse :* Rencontre équilibrée. Avantage à domicile pour {equipe_dom}.\n"
            message += f"💡 *Pronostic :* Plus de 1.5 buts / Victoire ou Nul {equipe_dom}\n\n"

        return message
    except Exception:
        return "⚠️ Erreur lors de la récupération des données de l'API Football."


# 5. COMMANDES DE DIALOGUE
@bot.message_handler(commands=["start"])
def send_welcome(msg):
    text = (
        "👋 **Bienvenue sur TRADING & PRONOSTICS BOT !**\n\n"
        "Cliquez sur la boîte de **Menu** en bas à gauche pour découvrir toutes les fonctionnalités !"
    )
    bot.reply_to(msg, text)


@bot.message_handler(commands=["help", "cmds"])
def send_help(msg):
    text = (
        "🤖 **MegaBot Trading & Sport v2.3**\n\n"
        "Voici la liste complète des fonctionnalités disponibles :\n\n"
        "📈 **Trading Crypto**\n"
        "• `/crypto` : Obtenir un signal en temps réel (menu interactif)\n"
        "• `/backtest` : Tester la stratégie technique sur 500 bougies\n\n"
        "⚽ **Football**\n"
        "• `/pari` : Analyses et conseils sur les 3 gros matchs du jour\n\n"
        "💰 **Gestion de Capital**\n"
        "• `/bankroll` : Calculateur de mise sécurisée (exposition à 2%)\n"
        f"{WARN}"
    )
    bot.reply_to(msg, text)


@bot.message_handler(commands=["crypto"])
def command_crypto(msg):
    markup = InlineKeyboardMarkup(row_width=2)
    b1 = InlineKeyboardButton("🟡 BTC/USDT", callback_data="c_BTCUSDT")
    b2 = InlineKeyboardButton("🔹 ETH/USDT", callback_data="c_ETHUSDT")
    b3 = InlineKeyboardButton("☀️ SOL/USDT", callback_data="c_SOLUSDT")
    b4 = InlineKeyboardButton("🔶 BNB/USDT", callback_data="c_BNBUSDT")
    b5 = InlineKeyboardButton("🌐 XRP/USDT", callback_data="c_XRPUSDT")
    b6 = InlineKeyboardButton("✍️ Autre symbole...", callback_data="c_custom")
    markup.add(b1, b2, b3, b4, b5, b6)

    bot.reply_to(
        msg,
        "📈 **ANALYSE CRYPTO EN TEMPS RÉEL**\n\n"
        "Sélectionne une paire ci-dessous ou clique sur *Autre symbole* :",
        reply_markup=markup
    )


@bot.message_handler(commands=["backtest"])
def command_backtest(msg):
    markup = InlineKeyboardMarkup(row_width=2)
    b1 = InlineKeyboardButton("🟡 BTC/USDT", callback_data="bt_BTCUSDT")
    b2 = InlineKeyboardButton("🔹 ETH/USDT", callback_data="bt_ETHUSDT")
    b3 = InlineKeyboardButton("☀️ SOL/USDT", callback_data="bt_SOLUSDT")
    b4 = InlineKeyboardButton("✍️ Autre symbole...", callback_data="bt_custom")
    markup.add(b1, b2, b3, b4)

    bot.reply_to(
        msg,
        "📉 **BACKTEST STRATÉGIE**\n\n"
        "Sélectionne une paire pour lancer la simulation sur 500 bougies :",
        reply_markup=markup
    )


@bot.message_handler(commands=["bankroll"])
def bankroll_start(msg):
    user_states[msg.chat.id] = "WAITING_BANKROLL"
    bot.reply_to(
        msg,
        "💰 **GESTION DE CAPITAL (Règle des 2%)**\n\n"
        "Entrez le montant de votre capital total (ex: `100000` ou `5000`) :"
    )


@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id

    if call.data.startswith("c_"):
        pair = call.data.replace("c_", "")
        if pair == "custom":
            user_states[chat_id] = "WAITING_CRYPTO_PAIR"
            bot.send_message(chat_id, "🔍 Tape le nom de la paire à analyser (ex: `PEPEUSDT`, `ADAUSDT`) :")
        else:
            bot.answer_callback_query(call.id, "Analyse du marché en cours...")
            bot.send_chat_action(chat_id, "typing")
            res = analyser_crypto(pair)
            bot.send_message(chat_id, res)

    elif call.data.startswith("bt_"):
        pair = call.data.replace("bt_", "")
        if pair == "custom":
            user_states[chat_id] = "WAITING_BACKTEST_PAIR"
            bot.send_message(chat_id, "🔍 Tape le nom de la paire pour le backtest (ex: `DOGEUSDT`, `LINKUSDT`) :")
        else:
            bot.answer_callback_query(call.id, "Calcul du backtest...")
            bot.send_chat_action(chat_id, "typing")
            res = executer_backtest(pair)
            bot.send_message(chat_id, res)


@bot.message_handler(commands=['pari', 'paris'])
def handle_paris(msg):
    bot.send_chat_action(msg.chat.id, 'typing')
    analyse = obtenir_analyses_matchs()
    bot.reply_to(msg, analyse, parse_mode="Markdown")


# ÉCOUTEUR TEXTE (Interactions fluides sans commande)
@bot.message_handler(func=lambda msg: not msg.text.startswith('/'))
def handle_text_messages(msg):
    chat_id = msg.chat.id
    state = user_states.get(chat_id)

    if state == "WAITING_BANKROLL":
        user_states[chat_id] = None
        cleaned_text = msg.text.replace(" ", "").replace(",", ".").strip()
        try:
            capital = float(cleaned_text)
            mise = capital * 0.02
            res = (
                f"💼 **CALCUL DE RISQUE (2% STRICT)**\n\n"
                f"💵 **Capital indiqué :** `{capital:,.2f}`\n"
                f"📊 **Risque autorisé :** `2%`\n"
                f"🎯 **Mise maximale recommandée :** `{mise:,.2f}`"
                f"{WARN}"
            )
            bot.send_message(chat_id, res)
        except ValueError:
            bot.send_message(chat_id, "❌ Veuillez entrer un nombre valide (ex: `100000`). Tapez `/bankroll` pour recommencer.")

    elif state == "WAITING_CRYPTO_PAIR":
        user_states[chat_id] = None
        bot.send_chat_action(chat_id, "typing")
        res = analyser_crypto(msg.text.strip())
        bot.send_message(chat_id, res)

    elif state == "WAITING_BACKTEST_PAIR":
        user_states[chat_id] = None
        bot.send_chat_action(chat_id, "typing")
        res = executer_backtest(msg.text.strip())
        bot.send_message(chat_id, res)


# 6. DÉMARRAGE DU BOT
if __name__ == "__main__":
    keep_alive()
    print("🤖 Bot démarré avec succès !")
    bot.infinity_polling(none_stop=True)