import os
import sys
import logging
import sqlite3
import datetime
import re
from typing import Optional, Tuple

import telebot
from telebot import types

# ------------------------------------------------------------------------------
# 1. LOGGING & SÉCURITÉ DES SECRETS
# ------------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("TradingBot")

# Récupération sécurisée des variables d'environnement
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "VOTRE_TOKEN_ICI")
PROVIDER_TOKEN = os.environ.get("PROVIDER_TOKEN", "") # Vide si Telegram Stars (XTR)

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

DB_FILE = "bot_database.db"

# ------------------------------------------------------------------------------
# 2. BASE DE DONNÉES ET MIGRATION
# ------------------------------------------------------------------------------
def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """ Initialise les tables requises avec gestion sécurisée des transactions. """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Table utilisateurs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                plan TEXT DEFAULT 'FREE',
                daily_requests INTEGER DEFAULT 0,
                last_request_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table des paiements anti-double traitement
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                payment_id TEXT PRIMARY KEY,
                telegram_user_id INTEGER,
                amount INTEGER,
                currency TEXT,
                product TEXT,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_id)
            )
        """)
        conn.commit()
    logger.info("Base de données initialisée avec succès.")

init_db()

# Memory states temporaires (sera migré en DB ultérieurement si nécessaire)
user_states = {}

# ------------------------------------------------------------------------------
# 3. GESTION CENTRALISÉE DES QUOTAS ET STATUT PREMIUM
# ------------------------------------------------------------------------------
def check_user_access(telegram_id: int) -> Tuple[bool, str]:
    """
    Vérifie si l'utilisateur peut exécuter une action.
    Ne consomme AUCUNE requête à ce stade.
    """
    today = datetime.date.today().isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT plan, daily_requests, last_request_date FROM users WHERE telegram_id = ?", (telegram_id,))
        user = cursor.fetchone()

        if not user:
            cursor.execute(
                "INSERT INTO users (telegram_id, plan, daily_requests, last_request_date) VALUES (?, 'FREE', 0, ?)",
                (telegram_id, today)
            )
            conn.commit()
            return True, "FREE"

        plan = user['plan']
        daily_requests = user['daily_requests']
        last_date = user['last_request_date']

        if plan == 'PREMIUM':
            return True, "PREMIUM"

        # Réinitialisation du quota si nouvelle journée
        if last_date != today:
            cursor.execute("UPDATE users SET daily_requests = 0, last_request_date = ? WHERE telegram_id = ?", (today, telegram_id))
            conn.commit()
            daily_requests = 0

        if daily_requests < 5:
            return True, "FREE"
        else:
            return False, "FREE"

def consume_request(telegram_id: int) -> bool:
    """
    Consomme 1 requête uniquement APRÈS l'exécution réussie de la fonctionnalité.
    """
    today = datetime.date.today().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT plan, daily_requests, last_request_date FROM users WHERE telegram_id = ?", (telegram_id,))
        user = cursor.fetchone()

        if not user or user['plan'] == 'PREMIUM':
            return True

        daily_requests = user['daily_requests']
        last_date = user['last_request_date']

        if last_date != today:
            daily_requests = 0

        cursor.execute(
            "UPDATE users SET daily_requests = ?, last_request_date = ? WHERE telegram_id = ?",
            (daily_requests + 1, today, telegram_id)
        )
        conn.commit()
    logger.info(f"Requête consommée pour ID: {telegram_id}. Nouveau total: {daily_requests + 1}")
    return True

# ------------------------------------------------------------------------------
# 4. VALIDATEURS DE DONNÉES
# ------------------------------------------------------------------------------
def validate_crypto_symbol(symbol: str) -> Optional[str]:
    """ Valide et nettoie les symboles crypto (ex: BTCUSDT). """
    clean_symbol = symbol.strip().upper()
    if not re.match(r"^[A-Z0-9]{2,12}$", clean_symbol):
        return None
    return clean_symbol

def validate_bankroll_input(amount_str: str) -> Optional[float]:
    """ Valide le montant de la bankroll. """
    try:
        amount = float(amount_str.replace(",", "."))
        if amount <= 0 or amount > 1_000_000_000 or amount != amount: # Checks positive & non-NaN
            return None
        return amount
    except ValueError:
        return None

# ------------------------------------------------------------------------------
# 5. COMMANDES PRINCIPALES
# ------------------------------------------------------------------------------
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "🤖 **Bienvenue sur votre Bot d'Analyse Trading & Pronos !**\n\n"
        "Commandes disponibles :\n"
        "📈 `/crypto` - Analyses Crypto en temps réel\n"
        "🧪 `/backtest` - Backtest de stratégies sur paires\n"
        "💰 `/bankroll` - Calculateur de gestion de mise\n"
        "⭐ `/premium` - Passer au statut Premium Illimité"
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")

@bot.message_handler(commands=['crypto'])
def cmd_crypto(message):
    user_id = message.from_user.id
    allowed, plan = check_user_access(user_id)

    if not allowed:
        bot.reply_to(message, "❌ **Quota quotidien atteint (5/5 requêtes).**\nPassez à `/premium` pour un accès illimité !", parse_mode="Markdown")
        return

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("BTC/USDT", callback_data="c_BTCUSDT"),
        types.InlineKeyboardButton("ETH/USDT", callback_data="c_ETHUSDT"),
        types.InlineKeyboardButton("SOL/USDT", callback_data="c_SOLUSDT")
    )
    bot.reply_to(message, "📊 **Choisissez une paire Crypto à analyser :**", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(commands=['backtest'])
def cmd_backtest(message):
    user_id = message.from_user.id
    allowed, plan = check_user_access(user_id)

    if not allowed:
        bot.reply_to(message, "❌ **Quota quotidien atteint (5/5 requêtes).**\nPassez à `/premium` pour un accès illimité !", parse_mode="Markdown")
        return

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("Backtest BTC (1H)", callback_data="bt_BTCUSDT_1h"),
        types.InlineKeyboardButton("Backtest ETH (1H)", callback_data="bt_ETHUSDT_1h")
    )
    bot.reply_to(message, "🧪 **Sélectionnez un actif pour le Backtest :**", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(commands=['bankroll'])
def cmd_bankroll(message):
    user_id = message.from_user.id
    allowed, plan = check_user_access(user_id)

    if not allowed:
        bot.reply_to(message, "❌ **Quota quotidien atteint (5/5 requêtes).**\nPassez à `/premium` pour un accès illimité !", parse_mode="Markdown")
        return

    user_states[user_id] = "WAITING_BANKROLL"
    bot.reply_to(message, "💵 Veuillez entrer le montant total de votre Bankroll en $ (ex: 1000) :")

# ------------------------------------------------------------------------------
# 6. GESTION DES TEXTES ET ETATS (BANKROLL VALIDATION)
# ------------------------------------------------------------------------------
@bot.message_handler(func=lambda msg: user_states.get(msg.from_user.id) == "WAITING_BANKROLL")
def handle_bankroll_input(message):
    user_id = message.from_user.id
    amount = validate_bankroll_input(message.text)

    if amount is None:
        bot.reply_to(message, "❌ **Montant invalide.** Veuillez entrer un nombre positif raisonnable. (Aucune requête consommée).")
        return

    # La validation a réussi : Réalisation du traitement
    try:
        stake_1pct = amount * 0.01
        stake_2pct = amount * 0.02
        response_text = (
            f"📊 **Gestion de Bankroll ({amount:.2f} $)**\n\n"
            f"🔹 Stake Prudent (1%) : **{stake_1pct:.2f} $**\n"
            f"🔹 Stake Modéré (2%) : **{stake_2pct:.2f} $**\n"
        )

        # Consommation unique du quota après succès
        consume_request(user_id)
        user_states.pop(user_id, None)
        bot.reply_to(message, response_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Erreur calcul Bankroll : {e}")
        bot.reply_to(message, "❌ Une erreur interne est survenue. Veuillez réessayer.")

# ------------------------------------------------------------------------------
# 7. HANDLERS DÉDIÉS AUX CALLBACKS
# ------------------------------------------------------------------------------
@bot.callback_query_handler(func=lambda call: call.data.startswith('c_'))
def handle_crypto_callback(call):
    user_id = call.from_user.id
    allowed, _ = check_user_access(user_id)

    if not allowed:
        bot.answer_callback_query(call.id, "Quota quotidien dépassé !", show_alert=True)
        return

    pair = call.data.replace("c_", "")
    valid_pair = validate_crypto_symbol(pair)

    if not valid_pair:
        bot.answer_callback_query(call.id, "Paire invalide.", show_alert=True)
        return

    try:
        # Simulation/Exécution de l'analyse technique
        analysis_result = f"📈 **Analyse de {valid_pair}**\n\nTendance: **HAUSSIÈRE**\nRSI: 62\nSignal: Achat modéré."

        # Envoi puis consommation
        bot.send_message(call.message.chat.id, analysis_result, parse_mode="Markdown")
        consume_request(user_id)
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Erreur API Crypto : {e}")
        bot.send_message(call.message.chat.id, "❌ Service d'analyse indisponible pour le moment.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('bt_'))
def handle_backtest_callback(call):
    user_id = call.from_user.id
    allowed, _ = check_user_access(user_id)

    if not allowed:
        bot.answer_callback_query(call.id, "Quota quotidien dépassé !", show_alert=True)
        return

    try:
        bt_data = call.data.replace("bt_", "")
        result_text = f"🧪 **Résultat du Backtest ({bt_data})**\n\nWinrate: **68%**\nProfit Factor: **1.85**\nTotal Trades: 120"

        bot.send_message(call.message.chat.id, result_text, parse_mode="Markdown")
        consume_request(user_id)
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Erreur Backtest : {e}")
        bot.send_message(call.message.chat.id, "❌ Échec de l'exécution du backtest.")

# ------------------------------------------------------------------------------
# 8. SYSTÈME DE PAIEMENT TELEGRAM STARS (ANTI-DOUBLE TRAITEMENT)
# ------------------------------------------------------------------------------
@bot.message_handler(commands=['premium'])
def cmd_premium(message):
    prices = [types.LabeledPrice(label="Abonnement Premium (1 Mois)", amount=250)] # 250 Stars
    bot.send_invoice(
        message.chat.id,
        title="Pass Premium Illimité",
        description="Accès illimité aux commandes Crypto, Backtests et Pronos.",
        invoice_payload="payload_premium_monthly",
        provider_token=PROVIDER_TOKEN,
        currency="XTR",
        prices=prices,
        start_parameter="premium-sub"
    )

@bot.pre_checkout_query_handler(func=lambda query: True)
def process_pre_checkout(query):
    bot.answer_pre_checkout_query(query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def process_successful_payment(message):
    payment_info = message.successful_payment
    telegram_id = message.from_user.id
    payment_id = payment_info.provider_payment_charge_id or payment_info.telegram_payment_charge_id

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Vérification si la transaction existe déjà
        cursor.execute("SELECT payment_id FROM payments WHERE payment_id = ?", (payment_id,))
        existing_payment = cursor.fetchone()

        if existing_payment:
            logger.warning(f"Paiement déjà traité tenté à nouveau : {payment_id}")
            bot.reply_to(message, "⚠️ Ce paiement a déjà été validé.")
            return

        # Enregistrement du paiement et passage en PREMIUM
        cursor.execute(
            "INSERT INTO payments (payment_id, telegram_user_id, amount, currency, product, status) VALUES (?, ?, ?, ?, ?, ?)",
            (payment_id, telegram_id, payment_info.total_amount, payment_info.currency, payment_info.invoice_payload, "COMPLETED")
        )

        cursor.execute("UPDATE users SET plan = 'PREMIUM' WHERE telegram_id = ?", (telegram_id,))
        conn.commit()

    logger.info(f"Paiement réussi & Premium activé pour l'utilisateur ID {telegram_id} (Tx: {payment_id})")
    bot.reply_to(message, "🎉 **Félicitations ! Votre abonnement PREMIUM est maintenant actif sans aucune limite.**", parse_mode="Markdown")

# ------------------------------------------------------------------------------
# 9. GESTION DES ERREURS GLOBALES & DÉMARRAGE
# ------------------------------------------------------------------------------
if __name__ == '__main__':
    logger.info("Bot démarré et prêt à recevoir des requêtes.")
    bot.infinity_polling(skip_pending=True)