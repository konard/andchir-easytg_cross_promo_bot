import logging
import math
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
        logger.info("The database has been initialized.")


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
/done *[канал]* - Сообщить владельцу канала о выполненном репосте
/confirm *[свой_канал]* *[канал_репоста]* - Подтвердить репост
/list - Список каналов, ожидающих подтверждения
/abuse *[канал]* *[причина]* - Пожаловаться на канал и владельца
/help - Показать эту справку

*Как это работает:*
1. Добавьте свой канал командой /add
2. Найдите похожие каналы /find
3. Подпишитесь и сделайте репост любого поста
4. Сообщите /done после репоста
5. Владелец канала подтвердит /confirm
6. Ожидайте ответного репоста
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')


# Command /add
async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "❌ Укажите имя канала.\n"
            "Пример: /add @mychannel"
        )
        return

    channel_username = context.args[0].strip()
    if not channel_username.startswith('@'):
        channel_username = '@' + channel_username

    try:
        # Getting information about the channel
        chat = await context.bot.get_chat(channel_username)

        # Checking if the bot is an administrator
        try:
            bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
            if bot_member.status not in ['administrator', 'creator']:
                await update.message.reply_text(
                    f"⚠️ Добавьте бота @{context.bot.username} администратором канала {channel_username} "
                    "с правом чтения сообщений, затем повторите команду."
                )
                return
        except Exception:
            await update.message.reply_text(
                f"⚠️ Добавьте бота @{context.bot.username} администратором канала {channel_username}, "
                "затем повторите команду."
            )
            return

        # We get the number of subscribers
        member_count = await context.bot.get_chat_member_count(chat.id)

        # Save in the database
        conn = Database.get_connection()
        if not conn:
            await update.message.reply_text("❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
            return

        cursor = conn.cursor()

        try:
            cursor.execute(
                "INSERT INTO channels (channel_username, channel_id, owner_user_id, subscriber_count) "
                "VALUES (%s, %s, %s, %s)",
                (channel_username, chat.id, user_id, member_count)
            )
            conn.commit()

            await update.message.reply_text(
                f"✅ Канал {channel_username} добавлен!\n"
                f"👥 Подписчиков: {member_count}"
            )
        except mysql.connector.IntegrityError:
            await update.message.reply_text(
                f"❌ Канал {channel_username} уже добавлен в каталог."
            )
        finally:
            cursor.close()
            conn.close()

    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        await update.message.reply_text(
            f"❌ Не удалось получить информацию о канале {channel_username}.\n"
            "Проверьте правильность имени и что канал публичный."
        )


# Command /my
async def my_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = Database.get_connection()
    if not conn:
        await update.message.reply_text("❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT channel_username, subscriber_count, added_date "
        "FROM channels WHERE owner_user_id = %s ORDER BY added_date DESC",
        (user_id,)
    )

    channels = cursor.fetchall()
    cursor.close()
    conn.close()

    if not channels:
        await update.message.reply_text("📭 У вас нет добавленных каналов.")
        return

    text = "📋 *Ваши каналы:*\n\n"
    for ch in channels:
        text += f"• *{ch['channel_username']}* - 👥 {ch['subscriber_count']} подписчиков\n"

    await update.message.reply_text(text, parse_mode='Markdown')


# Command /delete
async def delete_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "❌ Укажите имя канала.\n"
            "Пример: /delete @mychannel"
        )
        return

    channel_username = context.args[0].strip()
    if not channel_username.startswith('@'):
        channel_username = '@' + channel_username

    conn = Database.get_connection()
    if not conn:
        await update.message.reply_text("❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor()

    # Checking if the user is the owner
    cursor.execute(
        "SELECT id FROM channels WHERE channel_username = %s AND owner_user_id = %s",
        (channel_username, user_id)
    )

    if not cursor.fetchone():
        await update.message.reply_text(
            f"❌ Канал {channel_username} не найден или вы не являетесь владельцем."
        )
        cursor.close()
        conn.close()
        return

    cursor.execute(
        "DELETE FROM channels WHERE channel_username = %s AND owner_user_id = %s",
        (channel_username, user_id)
    )
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text(f"✅ Канал {channel_username} удалён из каталога.")


# Command /update
async def update_channel_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "❌ Укажите имя канала.\n"
            "Пример: /update @mychannel"
        )
        return

    channel_username = context.args[0].strip()
    if not channel_username.startswith('@'):
        channel_username = '@' + channel_username

    conn = Database.get_connection()
    if not conn:
        await update.message.reply_text("❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor(dictionary=True)

    # Checking if the user is the owner
    cursor.execute(
        "SELECT channel_id, subscriber_count FROM channels WHERE channel_username = %s AND owner_user_id = %s",
        (channel_username, user_id)
    )

    channel_data = cursor.fetchone()
    if not channel_data:
        await update.message.reply_text(
            f"❌ Канал {channel_username} не найден или вы не являетесь владельцем."
        )
        cursor.close()
        conn.close()
        return

    old_count = channel_data['subscriber_count']

    # We get the current number of subscribers
    try:
        chat = await context.bot.get_chat(channel_username)
        new_count = await context.bot.get_chat_member_count(chat.id)

        # Обновляем в базе данных
        cursor.execute(
            "UPDATE channels SET subscriber_count = %s WHERE channel_username = %s",
            (new_count, channel_username)
        )
        conn.commit()

        difference = new_count - old_count
        if difference > 0:
            change_text = f"📈 +{difference}"
        elif difference < 0:
            change_text = f"📉 {difference}"
        else:
            change_text = "➡️ без изменений"

        await update.message.reply_text(
            f"✅ Статистика канала {channel_username} обновлена!\n\n"
            f"👥 Было: {old_count}\n"
            f"👥 Стало: {new_count}\n"
            f"{change_text}"
        )

    except Exception as e:
        logger.error(f"Ошибка при обновлении статистики канала: {e}")
        await update.message.reply_text(
            f"❌ Не удалось получить информацию о канале {channel_username}.\n"
            "Убедитесь, что бот всё ещё является администратором канала."
        )
    finally:
        cursor.close()
        conn.close()


# Command /find
async def find_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите имя своего канала.\n"
            "Пример: /find @mychannel"
        )
        return

    channel_username = context.args[0].strip()
    if not channel_username.startswith('@'):
        channel_username = '@' + channel_username

    user_id = update.effective_user.id

    conn = Database.get_connection()
    if not conn:
        await update.message.reply_text("❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor(dictionary=True)

    # Getting subscribers to a user's channel
    cursor.execute(
        "SELECT subscriber_count FROM channels WHERE channel_username = %s",
        (channel_username,)
    )

    result = cursor.fetchone()
    if not result:
        await update.message.reply_text(
            f"❌ Канал {channel_username} не найден в каталоге.\n"
            "Добавьте его командой /add"
        )
        cursor.close()
        conn.close()
        return

    target_count = result['subscriber_count']
    diff = math.ceil(max(target_count, 100) * 0.2)

    # Looking for similar channels (±100 subscribers)
    cursor.execute(
        "SELECT channel_username, subscriber_count "
        "FROM channels "
        "WHERE channel_username != %s "
        "AND owner_user_id != %s "
        "AND subscriber_count BETWEEN %s AND %s "
        "ORDER BY RAND() LIMIT 10",
        (channel_username, user_id, max(target_count - diff, 0), target_count + diff)
    )

    channels = cursor.fetchall()
    cursor.close()
    conn.close()

    if not channels:
        await update.message.reply_text(
            "😔 К сожалению, не найдено каналов с похожей аудиторией.\n"
            "Попробуйте позже."
        )
        return

    text = f"🔍 *Найдено {len(channels)} похожих каналов:*\n\n"
    for ch in channels:
        text += f"• {ch['channel_username']} - 👥 {ch['subscriber_count']} подписчиков\n"

    text += "\n💡 Подпишитесь на канал, сделайте репост и используйте /done *[канал]*."

    await update.message.reply_text(text, parse_mode='Markdown')


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
    application.add_handler(CommandHandler("add", add_channel))
    application.add_handler(CommandHandler("my", my_channels))
    application.add_handler(CommandHandler("delete", delete_channel))
    application.add_handler(CommandHandler("update", update_channel_stats))
    application.add_handler(CommandHandler("find", find_channels))
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
