import os
import requests
import pandas as pd
import ta
from telebot import TeleBot, types

# ---------------------------------------------------------
# 1. INITIALISATION & SECRETS (Étape 2)
# ---------------------------------------------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.environ.get("ADMIN_ID", "0").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0

if not TOKEN:
    raise ValueError("❌ ERREUR: La variable TELEGRAM_BOT_TOKEN n'est pas configurée.")

bot = TeleBot(TOKEN)

# Avertissement légal réutilisable
WARN = "\n\n⚠️ *Gestion de risque obligatoire. Interdit aux -18 ans.*"


def is_admin(user_id):
    """Vérifie si l'utilisateur est l'administrateur configuré."""
    return user_id == ADMIN_ID


# ---------------------------------------------------------
# 2. FIX DÉFINITIF BINANCE (Contournement HTTP 451)
# ---------------------------------------------------------
def requete_binance_securisee(url_path):
    """Essaye plusieurs serveurs miroirs officiels pour contourner le blocage HTTP 451."""
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
    pair = pair.upper().replace("/", "").replace("-", "")
    url_path = f"/api/v3/klines?symbol={pair}&interval=1h&limit=100"

    data = requete_binance_securisee(url_path)
    if not data:
        return f"❌ Paire `{pair}` introuvable ou indisponible actuellement (Blocage réseau/serveur)."

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
        return "❌ Erreur de calcul des indicateurs."


def executer_backtest(pair):
    pair = pair.upper().replace("/", "").replace("-", "")
    url_path = f"/api/v3/klines?symbol={pair}&interval=1h&limit=500"

    data = requete_binance_securisee(url_path)
    if not data:
        return f"❌ Paire `{pair}` indisponible pour le backtest."

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
            f"🪙 **Paire :** `{pair}`\n"
            f"🔢 **Signaux exécutés :** `{total}`\n"
            f"✅ **Trades gagnants :** `{wins}`\n"
            f"📊 **Taux de réussite :** `{winrate:.1f}%`"
            f"{WARN}"
        )
    except Exception:
        return "❌ Erreur de calcul lors du backtest."


# ---------------------------------------------------------
# 3. GESTION DU CLAVIER & DES COMMANDES TELEGRAM
# ---------------------------------------------------------
def get_main_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_btc = types.InlineKeyboardButton("🟡 BTC/USDT", callback_data="crypto_BTCUSDT")
    btn_eth = types.InlineKeyboardButton("🔹 ETH/USDT", callback_data="crypto_ETHUSDT")
    btn_sol = types.InlineKeyboardButton("🔆 SOL/USDT", callback_data="crypto_SOLUSDT")
    btn_bnb = types.InlineKeyboardButton("🔸 BNB/USDT", callback_data="crypto_BNBUSDT")
    btn_xrp = types.InlineKeyboardButton("🌐 XRP/USDT", callback_data="crypto_XRPUSDT")
    btn_custom = types.InlineKeyboardButton("✍️ Autre symbole...", callback_data="crypto_CUSTOM")

    markup.add(btn_btc, btn_eth, btn_sol, btn_bnb, btn_xrp, btn_custom)
    return markup


@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    txt = (
        "👋 **Bienvenue sur TRADING & PRONO BOT !**\n\n"
        "Voici les commandes disponibles :\n"
        "📈 `/crypto` : Analyses & signaux sur les cryptomonnaies.\n"
        "💰 `/bankroll` : Calculateur de mise sécurisée.\n"
        "🔒 `/admin` : Espace d'administration réservé.\n"
        f"{WARN}"
    )
    bot.reply_to(message, txt, parse_mode="Markdown")


@bot.message_handler(commands=['crypto'])
def cmd_crypto(message):
    bot.send_message(
        message.chat.id,
        "📈 **ANALYSE CRYPTO EN TEMPS RÉEL**\n\nSélectionne une paire ci-dessous ou clique sur **Autre symbole** :",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['bankroll'])
def cmd_bankroll(message):
    txt = (
        "💰 **GESTION DE CAPITAL (BANKROLL)**\n\n"
        "Pour calculer votre mise avec une exposition recommandée de **2%** :\n"
        "Tapez : `/bankroll <votre_capital>`\n\n"
        "Example : `/bankroll 1000`"
        f"{WARN}"
    )

    parts = message.text.split()
    if len(parts) > 1:
        try:
            capital = float(parts[1])
            mise = capital * 0.02
            res = (
                f"🧮 **RÉSULTAT DU CALCUL**\n\n"
                f"💵 **Capital total :** `{capital:,.2f} $`\n"
                f"🛡️ **Mise recommandée (2%) :** `{mise:,.2f} $`"
                f"{WARN}"
            )
            bot.reply_to(message, res, parse_mode="Markdown")
            return
        except ValueError:
            bot.reply_to(message, "❌ Veuillez entrer un montant numérique valide.")
            return

    bot.reply_to(message, txt, parse_mode="Markdown")


# ---------------------------------------------------------
# 4. ESPACE ADMINISTRATEUR SÉCURISÉ (Étape 2)
# ---------------------------------------------------------
@bot.message_handler(commands=['admin'])
def cmd_admin(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ **Accès refusé.** Cette commande est réservée à l'administrateur.")
        return

    txt = (
        "⚙️ **ESPACE ADMINISTRATION**\n\n"
        " Statut : **Connecté en tant qu'administrateur**\n"
        "ID Admin : `{}`\n\n"
        "Vous disposez des droits complets sur le bot.".format(ADMIN_ID)
    )
    bot.reply_to(message, txt, parse_mode="Markdown")


# ---------------------------------------------------------
# 5. GESTION DES CALLBACKS (BOUTONS) & SAISIE MANUELLE
# ---------------------------------------------------------
@bot.callback_query_handler(func=lambda call: call.data.startswith('crypto_'))
def handle_crypto_callback(call):
    symbol = call.data.replace('crypto_', '')

    if symbol == "CUSTOM":
        msg = bot.send_message(
            call.message.chat.id,
            "✍️ Entrez le symbole de la crypto à analyser (ex: `ADAUSDT`, `DOGEUSDT`, `AVAXUSDT`) :"
        )
        bot.register_next_step_handler(msg, process_custom_symbol)
    else:
        bot.answer_callback_query(call.id, text=f"Analyse de {symbol} en cours...")
        res = analyser_crypto(symbol)
        bot.send_message(call.message.chat.id, res, parse_mode="Markdown")


def process_custom_symbol(message):
    symbol = message.text.strip().upper()
    res = analyser_crypto(symbol)
    bot.send_message(message.chat.id, res, parse_mode="Markdown")


# ---------------------------------------------------------
# 6. LANCEMENT DU BOT
# ---------------------------------------------------------
if __name__ == '__main__':
    print("🚀 Bot démarré avec succès !")
    bot.infinity_polling()