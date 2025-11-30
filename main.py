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
        "Используйте /help для просмотра всех команд.",
        parse_mode='Markdown'
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
/stat - Показать статистику бота
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
            "Пример: /add @mychannel",
            parse_mode='Markdown'
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
                    f"⚠️ Добавьте бота *@{context.bot.username}* администратором канала *{channel_username}* "
                    "с правом чтения сообщений, затем повторите команду.",
                    parse_mode='Markdown'
                )
                return
        except Exception:
            await update.message.reply_text(
                f"⚠️ Добавьте бота *@{context.bot.username}* администратором канала *{channel_username}*, "
                "затем повторите команду.",
                parse_mode='Markdown'
            )
            return

        # We get the number of subscribers
        member_count = await context.bot.get_chat_member_count(chat.id)

        # Save in the database
        conn = Database.get_connection()
        if not conn:
            await update.message.reply_text("❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.", parse_mode='Markdown')
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
                f"✅ Канал *{channel_username}* добавлен!\n"
                f"👥 Подписчиков: {member_count}",
                parse_mode='Markdown'
            )
        except mysql.connector.IntegrityError:
            await update.message.reply_text(
                f"❌ Канал *{channel_username}* уже добавлен в каталог.",
                parse_mode='Markdown'
            )
        finally:
            cursor.close()
            conn.close()

    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        await update.message.reply_text(
            f"❌ Не удалось получить информацию о канале *{channel_username}*.\n"
            "Проверьте правильность имени и что канал публичный.",
            parse_mode='Markdown'
        )


# Command /my
async def my_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = Database.get_connection()
    if not conn:
        await update.message.reply_text("❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.", parse_mode='Markdown')
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
        await update.message.reply_text("📭 У вас нет добавленных каналов.", parse_mode='Markdown')
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
            "Пример: /delete @mychannel",
            parse_mode='Markdown'
        )
        return

    channel_username = context.args[0].strip()
    if not channel_username.startswith('@'):
        channel_username = '@' + channel_username

    conn = Database.get_connection()
    if not conn:
        await update.message.reply_text("❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.", parse_mode='Markdown')
        return

    cursor = conn.cursor()

    # Checking if the user is the owner
    cursor.execute(
        "SELECT id FROM channels WHERE channel_username = %s AND owner_user_id = %s",
        (channel_username, user_id)
    )

    if not cursor.fetchone():
        await update.message.reply_text(
            f"❌ Канал *{channel_username}* не найден или вы не являетесь владельцем.",
            parse_mode='Markdown'
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

    await update.message.reply_text(f"✅ Канал *{channel_username}* удалён из каталога.", parse_mode='Markdown')


# Command /update
async def update_channel_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "❌ Укажите имя канала.\n"
            "Пример: /update @mychannel",
            parse_mode='Markdown'
        )
        return

    channel_username = context.args[0].strip()
    if not channel_username.startswith('@'):
        channel_username = '@' + channel_username

    conn = Database.get_connection()
    if not conn:
        await update.message.reply_text("❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.", parse_mode='Markdown')
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
            f"❌ Канал *{channel_username}* не найден или вы не являетесь владельцем.",
            parse_mode='Markdown'
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
            f"✅ Статистика канала *{channel_username}* обновлена!\n\n"
            f"👥 Было: {old_count}\n"
            f"👥 Стало: {new_count}\n"
            f"{change_text}",
            parse_mode='Markdown'
        )

    except Exception as e:
        logger.error(f"Ошибка при обновлении статистики канала: {e}")
        await update.message.reply_text(
            f"❌ Не удалось получить информацию о канале *{channel_username}*.\n"
            "Убедитесь, что бот всё ещё является администратором канала.",
            parse_mode='Markdown'
        )
    finally:
        cursor.close()
        conn.close()


# Command /find
async def find_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите имя своего канала.\n"
            "Пример: /find @mychannel",
            parse_mode='Markdown'
        )
        return

    channel_username = context.args[0].strip()
    if not channel_username.startswith('@'):
        channel_username = '@' + channel_username

    user_id = update.effective_user.id

    conn = Database.get_connection()
    if not conn:
        await update.message.reply_text("❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.", parse_mode='Markdown')
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
            f"❌ Канал *{channel_username}* не найден в каталоге.\n"
            "Добавьте его командой /add",
            parse_mode='Markdown'
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
            "Попробуйте позже.",
            parse_mode='Markdown'
        )
        return

    text = f"🔍 *Найдено {len(channels)} похожих каналов:*\n\n"
    for ch in channels:
        text += f"• *{ch['channel_username']}* - 👥 {ch['subscriber_count']} подписчиков\n"

    text += "\n💡 Подпишитесь на канал, сделайте репост и используйте /done *[канал]*."

    await update.message.reply_text(text, parse_mode='Markdown')


# Command /done
async def done_repost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "❌ Укажите имя канала, для которого сделали репост.\n"
            "Пример: /done @targetchannel",
            parse_mode='Markdown'
        )
        return

    to_channel = context.args[0].strip()
    if not to_channel.startswith('@'):
        to_channel = '@' + to_channel

    conn = Database.get_connection()
    if not conn:
        await update.message.reply_text("❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.", parse_mode='Markdown')
        return

    cursor = conn.cursor(dictionary=True)

    # Getting the user's channel
    cursor.execute(
        "SELECT channel_username FROM channels WHERE owner_user_id = %s LIMIT 1",
        (user_id,)
    )

    from_channel_result = cursor.fetchone()
    if not from_channel_result:
        await update.message.reply_text(
            "❌ У вас нет добавленных каналов. Используйте /add",
            parse_mode='Markdown'
        )
        cursor.close()
        conn.close()
        return

    from_channel = from_channel_result['channel_username']

    # Get the owner of the target channel
    cursor.execute(
        "SELECT owner_user_id FROM channels WHERE channel_username = %s",
        (to_channel,)
    )

    to_owner_result = cursor.fetchone()
    if not to_owner_result:
        await update.message.reply_text(
            f"❌ Канал *{to_channel}* не найден в каталоге",
            parse_mode='Markdown'
        )
        cursor.close()
        conn.close()
        return

    to_user_id = to_owner_result['owner_user_id']

    # Create a repost entry
    try:
        cursor.execute(
            "INSERT INTO reposts (from_channel, to_channel, from_user_id, to_user_id, status) "
            "VALUES (%s, %s, %s, %s, 'pending')",
            (from_channel, to_channel, user_id, to_user_id)
        )
        conn.commit()

        await update.message.reply_text(
            f"✅ Уведомление отправлено владельцу канала *{to_channel}*.\n"
            "Ожидайте подтверждения.",
            parse_mode='Markdown'
        )

        # Notify the channel owner
        try:
            await context.bot.send_message(
                chat_id=to_user_id,
                text=f"🔔 *Новое уведомление о репосте!*\n\n"
                     f"Канал *{from_channel}* сообщает, что сделал репост для *{to_channel}*.\n\n"
                     f"Проверьте и подтвердите командой:\n"
                     f"/confirm *{to_channel}* *{from_channel}*",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление: {e}")

    except mysql.connector.IntegrityError:
        await update.message.reply_text(
            f"❌ Запрос на подтверждение репоста уже существует",
            parse_mode='Markdown'
        )
    finally:
        cursor.close()
        conn.close()


# Command /confirm
async def confirm_repost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Укажите имя своего канала и канал, на котором сделан репост.\n"
            "Пример: /confirm *@mychannel* *@repost_channel*",
            parse_mode='Markdown'
        )
        return

    my_channel = context.args[0].strip()
    if not my_channel.startswith('@'):
        my_channel = '@' + my_channel

    repost_channel = context.args[1].strip()
    if not repost_channel.startswith('@'):
        repost_channel = '@' + repost_channel

    conn = Database.get_connection()
    if not conn:
        await update.message.reply_text("❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.", parse_mode='Markdown')
        return

    cursor = conn.cursor(dictionary=True)

    # Check that the user is the owner of their channel
    cursor.execute(
        "SELECT id FROM channels WHERE channel_username = %s AND owner_user_id = %s",
        (my_channel, user_id)
    )

    if not cursor.fetchone():
        await update.message.reply_text(
            f"❌ Канал *{my_channel}* не найден или вы не являетесь его владельцем",
            parse_mode='Markdown'
        )
        cursor.close()
        conn.close()
        return

    # Finding a pending repost
    cursor.execute(
        "SELECT r.id, r.from_channel, r.from_user_id "
        "FROM reposts r "
        "WHERE r.to_channel = %s AND r.from_channel = %s AND r.to_user_id = %s AND r.status = 'pending' "
        "LIMIT 1",
        (my_channel, repost_channel, user_id)
    )

    repost = cursor.fetchone()
    if not repost:
        await update.message.reply_text(
            f"❌ Нет ожидающих подтверждения репостов от канала *{repost_channel}* для *{my_channel}*.",
            parse_mode='Markdown'
        )
        cursor.close()
        conn.close()
        return

    # Updating the subscriber count on both channels.
    updated_counts = {}

    # Updating the subscribers of the channel that reposted.
    try:
        repost_chat = await context.bot.get_chat(repost_channel)
        repost_member_count = await context.bot.get_chat_member_count(repost_chat.id)

        cursor.execute(
            "UPDATE channels SET subscriber_count = %s WHERE channel_username = %s",
            (repost_member_count, repost_channel)
        )

        updated_counts[repost_channel] = repost_member_count
    except Exception as e:
        logger.error(f"Не удалось обновить количество подписчиков для {repost_channel}: {e}")

    # Updating your channel's subscribers
    try:
        my_chat = await context.bot.get_chat(my_channel)
        my_member_count = await context.bot.get_chat_member_count(my_chat.id)

        cursor.execute(
            "UPDATE channels SET subscriber_count = %s WHERE channel_username = %s",
            (my_member_count, my_channel)
        )

        updated_counts[my_channel] = my_member_count
    except Exception as e:
        logger.error(f"Не удалось обновить количество подписчиков для {my_channel}: {e}")

    # Confirming the repost
    cursor.execute(
        "UPDATE reposts SET status = 'confirmed', confirmed_date = NOW() WHERE id = %s",
        (repost['id'],)
    )
    conn.commit()
    cursor.close()
    conn.close()

    response_text = f"✅ Репост от канала *{repost_channel}* для вашего канала *{my_channel}* подтверждён!"
    if updated_counts:
        response_text += "\n\n📊 *Обновлена статистика:*"
        for channel, count in updated_counts.items():
            response_text += f"\n• *{channel}*: {count} подписчиков"

    await update.message.reply_text(response_text, parse_mode='Markdown')

    # Notify the author of the repost
    try:
        notification_text = (
            f"🎉 *Ваш репост подтверждён!*\n\n"
            f"Владелец канала *{my_channel}* подтвердил репост с вашего канала *{repost_channel}*."
        )
        if updated_counts:
            notification_text += "\n\n📊 *Обновлена статистика:*"
            for channel, count in updated_counts.items():
                notification_text += f"\n• *{channel}*: {count} подписчиков"

        await context.bot.send_message(
            chat_id=repost['from_user_id'],
            text=notification_text,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление: {e}")


# Command /list
async def list_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = Database.get_connection()
    if not conn:
        await update.message.reply_text("❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT r.from_channel, r.to_channel, r.created_date "
        "FROM reposts r "
        "WHERE r.to_user_id = %s AND r.status = 'pending' "
        "ORDER BY r.created_date DESC",
        (user_id,)
    )

    reposts = cursor.fetchall()
    cursor.close()
    conn.close()

    if not reposts:
        await update.message.reply_text("📭 Нет ожидающих подтверждения репостов.")
        return

    text = "📋 *Ожидают подтверждения:*\n\n"
    for r in reposts:
        date_str = r['created_date'].strftime('%d.%m.%Y %H:%M')
        text += f"• *{r['from_channel']}* → *{r['to_channel']}*\n  📅 {date_str}\n\n"

    text += "Используйте /confirm *[свой_канал]* *[канал_репоста]* для подтверждения."

    await update.message.reply_text(text, parse_mode='Markdown')


# Command /stat
async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = Database.get_connection()
    if not conn:
        await update.message.reply_text("❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor(dictionary=True)

    # Get total number of channels
    cursor.execute("SELECT COUNT(*) as total FROM channels")
    channels_count = cursor.fetchone()['total']

    # Get total number of confirmed reposts
    cursor.execute("SELECT COUNT(*) as total FROM reposts WHERE status = 'confirmed'")
    confirmed_count = cursor.fetchone()['total']

    # Get total number of pending reposts
    cursor.execute("SELECT COUNT(*) as total FROM reposts WHERE status = 'pending'")
    pending_count = cursor.fetchone()['total']

    cursor.close()
    conn.close()

    text = (
        "📊 *Статистика бота:*\n\n"
        f"📺 Всего каналов: *{channels_count}*\n"
        f"✅ Подтверждённых репостов: *{confirmed_count}*\n"
        f"⏳ Ожидают подтверждения: *{pending_count}*"
    )

    await update.message.reply_text(text, parse_mode='Markdown')


# Command /abuse
async def report_abuse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Укажите канал и причину жалобы.\n"
            "Пример: /abuse @badchannel Не делает репосты"
        )
        return

    channel_username = context.args[0].strip()
    if not channel_username.startswith('@'):
        channel_username = '@' + channel_username

    reason = ' '.join(context.args[1:])

    conn = Database.get_connection()
    if not conn:
        await update.message.reply_text("❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor()

    # Checking the existence of the channel
    cursor.execute(
        "SELECT id, owner_user_id FROM channels WHERE channel_username = %s",
        (channel_username,)
    )

    target_channel = cursor.fetchone()
    if not target_channel:
        await update.message.reply_text(
            f"❌ Канал *{channel_username}* не найден в каталоге.",
            parse_mode='Markdown'
        )
        cursor.close()
        conn.close()
        return

    if user_id == target_channel[1]:
        await update.message.reply_text(
            f"❌ Вы не можете пожаловаться на свой канал.",
        )
        cursor.close()
        conn.close()
        return

    # Saving the complaint
    cursor.execute(
        "INSERT INTO abuse_reports (reporter_user_id, channel_username, reason) "
        "VALUES (%s, %s, %s)",
        (user_id, channel_username, reason)
    )
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text(
        f"✅ Жалоба на канал *{channel_username}* зарегистрирована.\n"
        "Спасибо за информацию!",
        parse_mode='Markdown'
    )


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
    application.add_handler(CommandHandler("done", done_repost))
    application.add_handler(CommandHandler("confirm", confirm_repost))
    application.add_handler(CommandHandler("list", list_pending))
    application.add_handler(CommandHandler("stat", show_statistics))
    application.add_handler(CommandHandler("abuse", report_abuse))

    # Error handler
    application.add_error_handler(error_handler)

    # Launching the bot
    logger.info("The bot has been launched")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
