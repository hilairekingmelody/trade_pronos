import json
import os
from threading import Thread
from datetime import datetime

import requests
import ta
import telebot
from flask import Flask


app = Flask("")


@app.route("/")
def home() -> str:
    return "Bot N2 is alive!"


def run() -> None:
    port = int(os.getenv("KEEP_ALIVE_PORT", "5000"))
    app.run(host="0.0.0.0", port=port)


def keep_alive() -> None:
    thread = Thread(target=run, daemon=True)
    thread.start()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
FOOTBALL_KEY = os.getenv("FOOTBALL_API_KEY", "").strip()

if not TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN secret.")

bot = telebot.TeleBot(TOKEN)
DATA_FILE = "journal.json"
WARN = "⚠️ Risque de perte totale. Rien n’est sûr. -18 ans interdit. Trade/Mise responsable."

def load_journal():
    try:
        return json.load(open(DATA_FILE))
    except:
        return {"trades": []}

def save_journal(data):
    json.dump(data, open(DATA_FILE, "w"))

# ========== COMMANDES DE BASE ==========
@bot.message_handler(commands=['start'])
def start(msg):
    bot.reply_to(msg, f"**Bot N2 Trading + Stats v1.0** ✅\n\nTrading Crypto + Analyse Foot\nTape /cmds pour voir tout\n{WARN}", parse_mode="Markdown")

@bot.message_handler(commands=['cmds'])
def cmds(msg):
    texte = "**Commandes Disponibles:**\n"
    texte += "/crypto BTCUSDT → Signal RSI/MACD\n"
    texte += "/backtest BTCUSDT → Test stratégie\n"
    texte += "/match Real vs Barça → Stats H2H\n"
    texte += "/coupon → Matchs du jour\n"
    texte += "/bankroll 100000 2% → Calcule mise\n"
    texte += "/journal +2500 → Note un gain/perte\n\n" + WARN
    bot.send_message(msg.chat.id, texte, parse_mode="Markdown")

# ========== 1. MODULE TRADING ==========
@bot.message_handler(commands=['crypto'])
def signal_crypto(msg):
    try:
        pair = msg.text.split()[1].upper()
        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1h&limit=100"
        data = requests.get(url).json()
        closes = [float(c[4]) for c in data]
        rsi = ta.momentum.RSIIndicator(closes).rsi().iloc[-1]
        macd = ta.trend.MACD(closes).macd_diff().iloc[-1]

        if rsi<30 and macd>0: signal = "🟢 ACHAT Potentiel"
        elif rsi>70 and macd<0: signal = "🔴 VENTE Potentielle"
        else: signal = "🟡 NEUTRE - Attendre"

        bot.send_message(msg.chat.id, f"**{pair} - Timeframe 1H**\nPrix: {closes[-1]:.2f}\nRSI: {rsi:.1f} | MACD: {macd:.4f}\nSignal: {signal}\n\n{WARN}", parse_mode="Markdown")
    except:
        bot.reply_to(msg, "Format: /crypto BTCUSDT")

@bot.message_handler(commands=['backtest'])
def backtest(msg):
    try:
        pair = msg.text.split()[1].upper()
        data = requests.get(f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1h&limit=100").json()
        closes = [float(c[4]) for c in data]
        wins,total=0,0
        for i in range(50, len(closes)-1):
            rsi = ta.momentum.RSIIndicator(closes[:i]).rsi().iloc[-1]
            if rsi<30 and closes[i+1]>closes[i]: wins+=1
            if rsi>70 and closes[i+1]<closes[i]: wins+=1
            total+=1
        winrate = (wins/total)*100 if total>0 else 0
        bot.send_message(msg.chat.id, f"**Backtest {pair} 1H**\nTrades simulés: {total}\nWinrate: {winrate:.1f}%\n\n{WARN}", parse_mode="Markdown")
    except:
        bot.reply_to(msg, "Format: /backtest ETHUSDT")

# ========== 2. MODULE PARIS SPORTIFS ==========
@bot.message_handler(commands=['match'])
def match(msg):
    try:
        equipes = msg.text.replace("/match ", "").split(" vs ")
        e1, e2 = equipes[0].strip(), equipes[1].strip()
        headers = {"x-apisports-key": FOOTBALL_KEY}

        r1 = requests.get(f"https://v3.football.api-sports.io/teams?search={e1}", headers=headers).json()
        r2 = requests.get(f"https://v3.football.api-sports.io/teams?search={e2}", headers=headers).json()
        if not r1['response'] or not r2['response']: return bot.reply_to(msg, "Équipe introuvable")

        id1, id2 = r1['response'][0]['team']['id'], r2['response'][0]['team']['id']
        h2h = requests.get(f"https://v3.football.api-sports.io/fixtures/headtohead?h2h={id1}-{id2}&last=5", headers=headers).json()

        texte = f"**{e1} vs {e2} - 5 derniers H2H**\n\n"
        for m in h2h['response']:
            home = m['teams']['home']['name']
            away = m['teams']['away']['name']
            score = f"{m['goals']['home']}-{m['goals']['away']}"
            texte += f"{home} {score} {away}\n"
        texte += f"\n{WARN}"
        bot.send_message(msg.chat.id, texte, parse_mode="Markdown")
    except:
        bot.reply_to(msg, "Format: /match PSG vs OM")

@bot.message_handler(commands=['coupon'])
def coupon(msg):
    headers = {"x-apisports-key": FOOTBALL_KEY}
    date = datetime.now().strftime("%Y-%m-%d")
    url = f"https://v3.football.api-sports.io/fixtures?league=39&season=2024&date={date}" # PL
    data = requests.get(url, headers=headers).json()
    texte = f"**Matchs PL du {date}:**\n"
    for m in data['response'][:3]:
        texte += f"• {m['teams']['home']['name']} vs {m['teams']['away']['name']}\n"
    texte += f"\nFais /match EquipeA vs EquipeB pour analyse\n{WARN}"
    bot.send_message(msg.chat.id, texte, parse_mode="Markdown")

# ========== 3. MODULE GESTION ==========
@bot.message_handler(commands=['bankroll'])
def bankroll(msg):
    try:
        capital, risk = msg.text.split()[1:]
        capital, risk = float(capital), float(risk.replace("%",""))
        if risk > 5:
            return bot.reply_to(msg, "⚠️ Risque >5% refusé. Protège ton capital.")
        mise = capital * risk / 100
        bot.reply_to(msg, f"Capital: {capital} FCFA\nRisque: {risk}%\nMise conseillée: {mise} FCFA")
    except:
        bot.reply_to(msg, "Format: /bankroll 100000 2%")

@bot.message_handler(commands=['journal'])
def journal(msg):
    data = load_journal()
    parts = msg.text.split()
    if len(parts) > 1:
        gain = float(parts[1].replace("+",""))
        data["trades"].append({"date": str(datetime.now().date()), "gain": gain})
        save_journal(data)
        bot.reply_to(msg, f"✅ Noté: {gain} FCFA")
    total = sum(t["gain"] for t in data["trades"][-30:])
    bot.send_message(msg.chat.id, f"**Journal 30 jours:** {total} FCFA\nTrades: {len(data['trades'])}\n\n{WARN}", parse_mode="Markdown")

keep_alive()
bot.polling()