import logging
import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
import mysql.connector
from mysql.connector import Error
import random
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DB_CONFIG = {
    'host': 'localhost',
    'database': os.environ['DB_NAME'],
    'user': os.environ['DB_USER_NAME'],
    'password': os.environ['DB_USER_PASSWORD']
}

BOT_TOKEN = os.environ['BOT_TOKEN']


class Database:

    @staticmethod
    def get_connection():
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            return conn
        except Error as e:
            logger.error(f"Error connecting to the database: {e}")
            return None

    @staticmethod
    def init_db():
        conn = Database.get_connection()
        if not conn:
            return

        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                id INT AUTO_INCREMENT PRIMARY KEY,
                channel_username VARCHAR(255) UNIQUE NOT NULL,
                channel_id BIGINT,
                owner_user_id BIGINT NOT NULL,
                subscriber_count INT NOT NULL,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_owner (owner_user_id),
                INDEX idx_subs (subscriber_count)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reposts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                from_channel VARCHAR(255) NOT NULL,
                to_channel VARCHAR(255) NOT NULL,
                from_user_id BIGINT NOT NULL,
                to_user_id BIGINT NOT NULL,
                status ENUM('pending', 'confirmed', 'rejected') DEFAULT 'pending',
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                confirmed_date TIMESTAMP NULL,
                INDEX idx_status (status),
                INDEX idx_to_user (to_user_id),
                FOREIGN KEY (from_channel) REFERENCES channels(channel_username) ON DELETE CASCADE,
                FOREIGN KEY (to_channel) REFERENCES channels(channel_username) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS abuse_reports (
                id INT AUTO_INCREMENT PRIMARY KEY,
                reporter_user_id BIGINT NOT NULL,
                channel_username VARCHAR(255) NOT NULL,
                reason TEXT NOT NULL,
                report_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_channel (channel_username)
            )
        ''')

        conn.commit()
        cursor.close()
        conn.close()
        logger.info("База данных инициализирована")


# Command /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Добро пожаловать в бот обмена аудиторией!\n\n"
        "Используйте /help для просмотра всех команд."
    )


# Command /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📚 *Справка по командам:*

/add - Добавить свой канал в каталог
/my - Показать мои каналы
/delete *[канал]* - Удалить канал из каталога
/update *[канал]* - Обновить количество подписчиков
/find *[канал]* - Найти похожие каналы для обмена
/done *[канал]* - Сообщить о выполненном репосте
/confirm *[свой_канал]* *[канал_репоста]* - Подтвердить репост
/list - Список каналов, ожидающих подтверждения
/abuse *[канал]* *[причина]* - Пожаловаться на канал и владельца
/help - Показать эту справку

*Как это работает:*
1. Добавьте свой канал командой /add
2. Найдите похожие каналы /find
3. Подпишитесь и сделайте репост
4. Сообщите /done после репоста
5. Владелец канала подтвердит /confirm
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')


# Error handler
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")


def main():
    # Database initialization
    Database.init_db()

    # Creating an application
    application = Application.builder().token(BOT_TOKEN).build()

    # Registering command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    # application.add_handler(CommandHandler("add", add_channel))
    # application.add_handler(CommandHandler("my", my_channels))
    # application.add_handler(CommandHandler("delete", delete_channel))
    # application.add_handler(CommandHandler("update", update_channel_stats))
    # application.add_handler(CommandHandler("find", find_channels))
    # application.add_handler(CommandHandler("done", done_repost))
    # application.add_handler(CommandHandler("confirm", confirm_repost))
    # application.add_handler(CommandHandler("list", list_pending))
    # application.add_handler(CommandHandler("abuse", report_abuse))

    # Error handler
    application.add_error_handler(error_handler)

    # Launching the bot
    logger.info("The bot has been launched")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
