    import json
    import os
    from datetime import datetime
    from threading import Thread

    from flask import Flask
    import pandas as pd
    import requests
    import ta
    import telebot

    # 1. SERVEUR WEB (Pour garder Replit/Render éveillé)
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

    print(f"🔑 Longueur du Token détecté : {len(TOKEN)} caractères")

    if not TOKEN:
        raise ValueError("ERREUR: La variable TELEGRAM_BOT_TOKEN est introuvable dans les Secrets !")

    bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
    DATA_FILE = "journal.json"
    WARN = "\n\n⚠️ *Attention :* Gestion de risque obligatoire. Interdit aux -18 ans."


    # 3. GESTION DU JOURNAL
    def load_journal():
        if not os.path.exists(DATA_FILE):
            return {"trades": []}
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"trades": []}


    def save_journal(data):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


    # 4. FONCTION D'ANALYSE DE FOOTBALL
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


    # 5. COMMANDES DU BOT
    @bot.message_handler(commands=["start", "help", "cmds"])
    def send_welcome(msg):
        text = (
            "🤖 **MegaBot Trading & Sport v2.0**\n\n"
            "Voici les commandes disponibles :\n\n"
            "📈 **Trading**\n"
            "• `/crypto BTCUSDT` : Analyse RSI + MACD (1H)\n"
            "• `/backtest ETHUSDT` : Test rapide de stratégie\n\n"
            "⚽ **Football**\n"
            "• `/pari` : Analyses des 3 gros matchs du jour\n"
            "• `/match PSG vs Marseille` : Historique des 5 derniers H2H\n"
            "• `/coupon` : Matchs Premier League du jour\n\n"
            "💰 **Gestion**\n"
            "• `/bankroll 100000 2%` : Calcul de mise sécurisée\n"
            "• `/journal +2500` : Enregistrer un gain ou une perte\n"
            f"{WARN}"
        )
        bot.reply_to(msg, text)


    @bot.message_handler(commands=['pari', 'paris'])
    def handle_paris(msg):
        bot.send_chat_action(msg.chat.id, 'typing')
        analyse = obtenir_analyses_matchs()
        bot.reply_to(msg, analyse, parse_mode="Markdown")


    @bot.message_handler(commands=["crypto"])
    def signal_crypto(msg):
        parts = msg.text.split()
        if len(parts) < 2:
            return bot.reply_to(
                msg, "❌ Usage : `/crypto BTCUSDT` (Exemple : `/crypto ETHUSDT`)"
            )

        pair = parts[1].upper()
        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1h&limit=100"

        try:
            res = requests.get(url, timeout=10)
            if res.status_code != 200:
                return bot.reply_to(msg, f"❌ Paire `{pair}` non trouvée sur Binance.")

            data = res.json()
            closes = pd.Series([float(c[4]) for c in data])

            rsi = ta.momentum.RSIIndicator(closes).rsi().iloc[-1]
            macd = ta.trend.MACD(closes).macd_diff().iloc[-1]
            price = closes.iloc[-1]

            if rsi < 30 and macd > 0:
                signal = "🟢 **ACHAT POTENTIEL**"
            elif rsi > 70 and macd < 0:
                signal = "🔴 **VENTE POTENTIELLE**"
            else:
                signal = "🟡 **NEUTRE**"

            text = (
                f"📊 **{pair} (1H)**\n"
                f"Prix : `{price:.2f}`\n"
                f"RSI : `{rsi:.1f}` | MACD : `{macd:.4f}`\n\n"
                f"Signal : {signal}{WARN}"
            )
            bot.reply_to(msg, text)
        except Exception:
            bot.reply_to(msg, "❌ Erreur lors de la récupération des données Binance.")


    @bot.message_handler(commands=["backtest"])
    def backtest(msg):
        parts = msg.text.split()
        if len(parts) < 2:
            return bot.reply_to(msg, "❌ Usage : `/backtest BTCUSDT`")

        pair = parts[1].upper()
        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1h&limit=300"

        try:
            res = requests.get(url, timeout=10)
            data = res.json()
            closes = pd.Series([float(c[4]) for c in data])
            rsi_series = ta.momentum.RSIIndicator(closes).rsi()

            wins, total = 0, 0
            for i in range(30, len(closes) - 1):
                r = rsi_series.iloc[i]
                if r < 30:
                    total += 1
                    if closes.iloc[i + 1] > closes.iloc[i]:
                        wins += 1
                elif r > 70:
                    total += 1
                    if closes.iloc[i + 1] < closes.iloc[i]:
                        wins += 1

            winrate = (wins / total * 100) if total > 0 else 0
            text = (
                f"📈 **Backtest RSI sur {pair}**\n"
                f"Trades simulés : `{total}`\n"
                f"Taux de réussite : `{winrate:.1f}%`{WARN}"
            )
            bot.reply_to(msg, text)
        except Exception:
            bot.reply_to(msg, "❌ Erreur pendant le backtest.")


    @bot.message_handler(commands=["match"])
    def match(msg):
        content = msg.text.replace("/match", "").strip()
        if " vs " not in content:
            return bot.reply_to(msg, "❌ Usage : `/match Real Madrid vs Barcelona`")

        e1, e2 = [x.strip() for x in content.split(" vs ")]
        headers = {"x-apisports-key": FOOTBALL_KEY}

        try:
            r1 = requests.get(
                f"https://v3.football.api-sports.io/teams?search={e1}",
                headers=headers,
                timeout=10,
            ).json()
            r2 = requests.get(
                f"https://v3.football.api-sports.io/teams?search={e2}",
                headers=headers,
                timeout=10,
            ).json()

            if not r1.get("response") or not r2.get("response"):
                return bot.reply_to(msg, "❌ Équipe introuvable.")

            id1 = r1["response"][0]["team"]["id"]
            id2 = r2["response"][0]["team"]["id"]

            h2h = requests.get(
                f"https://v3.football.api-sports.io/fixtures/headtohead?h2h={id1}-{id2}&last=5",
                headers=headers,
                timeout=10,
            ).json()

            text = f"⚽ **{e1} vs {e2} (5 Derniers H2H)**\n\n"
            for m in h2h.get("response", []):
                text += f"• {m['teams']['home']['name']} `{m['goals']['home']}-{m['goals']['away']}` {m['teams']['away']['name']}\n"

            text += WARN
            bot.reply_to(msg, text)
        except Exception:
            bot.reply_to(msg, "❌ Erreur de recherche H2H.")


    @bot.message_handler(commands=["coupon"])
    def coupon(msg):
        headers = {"x-apisports-key": FOOTBALL_KEY}
        date_str = datetime.now().strftime("%Y-%m-%d")

        url = f"https://v3.football.api-sports.io/fixtures?league=39&season=2026&date={date_str}"

        try:
            res = requests.get(url, headers=headers, timeout=10).json()
            matches = res.get("response", [])

            if not matches:
                return bot.reply_to(
                    msg, f"ℹ️ Aucun match de Premier League aujourd'hui ({date_str})."
                )

            text = f"⚽ **Premier League ({date_str})**\n\n"
            for m in matches[:5]:
                text += f"• {m['teams']['home']['name']} vs {m['teams']['away']['name']}\n"

            text += WARN
            bot.reply_to(msg, text)
        except Exception:
            bot.reply_to(msg, "❌ Erreur lors du chargement des matchs.")


    @bot.message_handler(commands=["bankroll"])
    def bankroll(msg):
        try:
            parts = msg.text.split()
            capital = float(parts[1])
            risk = float(parts[2].replace("%", ""))

            if risk > 5:
                return bot.reply_to(
                    msg, "⚠️ Risque trop élevé (>5%). Protégez votre capital !"
                )

            mise = capital * (risk / 100)
            bot.reply_to(
                msg,
                f"💼 **Gestion de Risque**\nCapital : `{capital:,.0f} FCFA`\nRisque : `{risk}%`\n👉 **Mise conseillée : `{mise:,.0f} FCFA`**",
            )
        except Exception:
            bot.reply_to(msg, "❌ Usage : `/bankroll 100000 2%`")


    @bot.message_handler(commands=["journal"])
    def journal(msg):
        data = load_journal()
        parts = msg.text.split()

        if len(parts) > 1:
            try:
                gain = float(parts[1].replace("+", ""))
                data["trades"].append({
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "gain": gain,
                })
                save_journal(data)
                bot.reply_to(msg, f"✅ Opération notée : `{gain:+.0f} FCFA`")
            except ValueError:
                pass

        total = sum(t["gain"] for t in data["trades"][-30:])
        bot.reply_to(
            msg,
            f"📖 **Journal (30 derniers trades)**\nBénéfice/Perte cumulé : `{total:+.0f} FCFA`\nNombre de trades : `{len(data['trades'])}`{WARN}",
        )


    # 6. DÉMARRAGE DU BOT
    if __name__ == "__main__":
        keep_alive()
        print("🤖 Bot démarré avec succès !")
        bot.infinity_polling(none_stop=True)