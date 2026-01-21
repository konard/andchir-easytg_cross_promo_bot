import logging
import math
import os
import json
import re

from flask import Flask, request, render_template_string, redirect, url_for, session
import mysql.connector
from mysql.connector import Error
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

VK_DB_CONFIG = {
    'host': 'localhost',
    'database': os.environ['VK_DB_NAME'],
    'user': os.environ['VK_DB_USER_NAME'],
    'password': os.environ['VK_DB_USER_PASSWORD']
}

VK_ACCESS_TOKEN = os.environ['VK_ACCESS_TOKEN']
VK_GROUP_ID = os.environ['VK_GROUP_ID']
VK_CONFIRMATION_CODE = os.environ['VK_CONFIRMATION_CODE']
VK_FLASK_HOST = os.environ.get('VK_FLASK_HOST', '0.0.0.0')
VK_FLASK_PORT = int(os.environ.get('VK_FLASK_PORT', '5000'))

# Admin interface configuration
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')

# Telegram database configuration (for admin interface)
TG_DB_CONFIG = {
    'host': 'localhost',
    'database': os.environ.get('DB_NAME', ''),
    'user': os.environ.get('DB_USER_NAME', ''),
    'password': os.environ.get('DB_USER_PASSWORD', '')
}

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(24))


class VKDatabase:

    @staticmethod
    def get_connection():
        try:
            conn = mysql.connector.connect(**VK_DB_CONFIG)
            return conn
        except Error as e:
            logger.error(f"Error connecting to the VK database: {e}")
            return None

    @staticmethod
    def init_db():
        conn = VKDatabase.get_connection()
        if not conn:
            return

        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS vk_channels (
                id INT AUTO_INCREMENT PRIMARY KEY,
                channel_username VARCHAR(255) UNIQUE NOT NULL,
                channel_id VARCHAR(255),
                owner_user_id BIGINT NOT NULL,
                subscriber_count INT NOT NULL,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_owner (owner_user_id),
                INDEX idx_subs (subscriber_count)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS vk_reposts (
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
                FOREIGN KEY (from_channel) REFERENCES vk_channels(channel_username) ON DELETE CASCADE,
                FOREIGN KEY (to_channel) REFERENCES vk_channels(channel_username) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS vk_abuse_reports (
                id INT AUTO_INCREMENT PRIMARY KEY,
                reporter_user_id BIGINT NOT NULL,
                channel_username VARCHAR(255) NOT NULL,
                reason TEXT NOT NULL,
                report_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_channel (channel_username)
            )
        ''')

        # Add repost_channel column if it doesn't exist
        try:
            cursor.execute('''
                ALTER TABLE vk_reposts
                ADD COLUMN repost_channel VARCHAR(255) NULL AFTER to_channel
            ''')
            conn.commit()
            logger.info("Added repost_channel column to vk_reposts table")
        except mysql.connector.Error as err:
            if err.errno == 1060:  # Duplicate column name
                pass
            else:
                logger.error(f"Error adding repost_channel column: {err}")

        conn.commit()
        cursor.close()
        conn.close()
        logger.info("The VK database has been initialized.")


class TGDatabase:
    """Telegram database access for admin interface"""

    @staticmethod
    def get_connection():
        if not TG_DB_CONFIG['database']:
            return None
        try:
            conn = mysql.connector.connect(**TG_DB_CONFIG)
            return conn
        except Error as e:
            logger.error(f"Error connecting to the Telegram database: {e}")
            return None


# Admin interface constants
ITEMS_PER_PAGE = 20


def get_admin_base_template():
    """Return the base HTML template for admin interface"""
    return '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Админ-панель бота</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-sRIl4kxILFvY47J16cr9ZwB07vP4J8+LH7qKQnuqkuIAvNWLzeN8tE5YBujZqJLB" crossorigin="anonymous">
    <style>
        .nav-pills .nav-link.active { background-color: #0d6efd; }
        .table-responsive { margin-top: 20px; }
        .pagination { margin-top: 20px; }
        .search-form { margin-bottom: 20px; }
    </style>
</head>
<body>
    <div class="container mt-4">
        <h1 class="mb-4">Админ-панель бота</h1>
        {% block content %}{% endblock %}
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/js/bootstrap.bundle.min.js" integrity="sha384-T3BcNqdY3F5V4Y3mL9A8u1a3ZC6iO6p3kfH1sF5BReGc3c0E6C9e8D2F6GRtI3Rv" crossorigin="anonymous"></script>
</body>
</html>
'''


def get_login_template():
    """Return the login page template"""
    return '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Вход в админ-панель</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-sRIl4kxILFvY47J16cr9ZwB07vP4J8+LH7qKQnuqkuIAvNWLzeN8tE5YBujZqJLB" crossorigin="anonymous">
</head>
<body>
    <div class="container mt-5">
        <div class="row justify-content-center">
            <div class="col-md-4">
                <div class="card">
                    <div class="card-header">
                        <h4 class="mb-0">Вход в админ-панель</h4>
                    </div>
                    <div class="card-body">
                        {% if error %}
                        <div class="alert alert-danger">{{ error }}</div>
                        {% endif %}
                        <form method="POST">
                            <div class="mb-3">
                                <label for="password" class="form-label">Пароль</label>
                                <input type="password" class="form-control" id="password" name="password" required>
                            </div>
                            <button type="submit" class="btn btn-primary w-100">Войти</button>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
'''


def get_admin_template():
    """Return the main admin page template"""
    return '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Админ-панель бота</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-sRIl4kxILFvY47J16cr9ZwB07vP4J8+LH7qKQnuqkuIAvNWLzeN8tE5YBujZqJLB" crossorigin="anonymous">
    <style>
        .nav-pills .nav-link.active { background-color: #0d6efd; }
        .table-responsive { margin-top: 20px; }
        .pagination { margin-top: 20px; }
        .search-form { margin-bottom: 20px; }
        .sub-nav { margin-top: 20px; margin-bottom: 20px; }
        .delete-btn { font-size: 0.875rem; padding: 0.25rem 0.5rem; }
    </style>
</head>
<body>
    <div class="container mt-4">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h1>Админ-панель бота</h1>
            <a href="{{ url_for('admin_logout') }}" class="btn btn-outline-secondary">Выйти</a>
        </div>

        <!-- Platform tabs -->
        <ul class="nav nav-pills mb-3" id="platformTabs" role="tablist">
            <li class="nav-item" role="presentation">
                <a class="nav-link {% if platform == 'telegram' %}active{% endif %}" href="{{ url_for('bot_admin', platform='telegram', section=section) }}">Телеграм</a>
            </li>
            <li class="nav-item" role="presentation">
                <a class="nav-link {% if platform == 'vk' %}active{% endif %}" href="{{ url_for('bot_admin', platform='vk', section=section) }}">ВКонтакте</a>
            </li>
        </ul>

        <!-- Section tabs -->
        <ul class="nav nav-tabs sub-nav" id="sectionTabs" role="tablist">
            <li class="nav-item" role="presentation">
                <a class="nav-link {% if section == 'channels' %}active{% endif %}" href="{{ url_for('bot_admin', platform=platform, section='channels') }}">Группы</a>
            </li>
            <li class="nav-item" role="presentation">
                <a class="nav-link {% if section == 'reposts' %}active{% endif %}" href="{{ url_for('bot_admin', platform=platform, section='reposts') }}">Репосты</a>
            </li>
            <li class="nav-item" role="presentation">
                <a class="nav-link {% if section == 'reports' %}active{% endif %}" href="{{ url_for('bot_admin', platform=platform, section='reports') }}">Жалобы</a>
            </li>
        </ul>

        {% if section == 'channels' %}
        <!-- Search form for channels -->
        <form class="search-form" method="GET">
            <input type="hidden" name="platform" value="{{ platform }}">
            <input type="hidden" name="section" value="{{ section }}">
            <div class="row">
                <div class="col-md-4">
                    <div class="input-group">
                        <input type="text" class="form-control" name="search" value="{{ search }}" placeholder="Поиск по названию...">
                        <button class="btn btn-outline-primary" type="submit">Поиск</button>
                        {% if search %}
                        <a href="{{ url_for('bot_admin', platform=platform, section=section) }}" class="btn btn-outline-secondary">Сбросить</a>
                        {% endif %}
                    </div>
                </div>
            </div>
        </form>
        {% endif %}

        {% if error %}
        <div class="alert alert-warning">{{ error }}</div>
        {% endif %}

        {% if items %}
        <div class="table-responsive">
            {% if section == 'channels' %}
            <table class="table table-striped table-hover">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Название</th>
                        <th>ID канала</th>
                        <th>ID владельца</th>
                        <th>Подписчиков</th>
                        <th>Дата добавления</th>
                        <th>Действия</th>
                    </tr>
                </thead>
                <tbody>
                    {% for item in items %}
                    <tr>
                        <td>{{ item.id }}</td>
                        <td>{{ item.channel_username }}</td>
                        <td>{{ item.channel_id or '-' }}</td>
                        <td>{{ item.owner_user_id }}</td>
                        <td>{{ item.subscriber_count }}</td>
                        <td>{{ item.added_date.strftime('%d.%m.%Y %H:%M') if item.added_date else '-' }}</td>
                        <td>
                            <form method="POST" action="{{ url_for('admin_delete_channel') }}" style="display: inline;" onsubmit="return confirm('Вы уверены, что хотите удалить канал {{ item.channel_username }}? Все связанные репосты также будут удалены.');">
                                <input type="hidden" name="platform" value="{{ platform }}">
                                <input type="hidden" name="channel_id" value="{{ item.id }}">
                                <input type="hidden" name="return_page" value="{{ page }}">
                                <input type="hidden" name="return_search" value="{{ search }}">
                                <button type="submit" class="btn btn-danger btn-sm delete-btn">Удалить</button>
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% elif section == 'reposts' %}
            <table class="table table-striped table-hover">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>От канала</th>
                        <th>Для канала</th>
                        <th>Канал репоста</th>
                        <th>ID отправителя</th>
                        <th>ID получателя</th>
                        <th>Статус</th>
                        <th>Дата создания</th>
                        <th>Дата подтверждения</th>
                        <th>Действия</th>
                    </tr>
                </thead>
                <tbody>
                    {% for item in items %}
                    <tr>
                        <td>{{ item.id }}</td>
                        <td>{{ item.from_channel }}</td>
                        <td>{{ item.to_channel }}</td>
                        <td>{{ item.repost_channel or '-' }}</td>
                        <td>{{ item.from_user_id }}</td>
                        <td>{{ item.to_user_id }}</td>
                        <td>
                            {% if item.status == 'pending' %}
                            <span class="badge bg-warning">Ожидает</span>
                            {% elif item.status == 'confirmed' %}
                            <span class="badge bg-success">Подтверждён</span>
                            {% elif item.status == 'rejected' %}
                            <span class="badge bg-danger">Отклонён</span>
                            {% else %}
                            {{ item.status }}
                            {% endif %}
                        </td>
                        <td>{{ item.created_date.strftime('%d.%m.%Y %H:%M') if item.created_date else '-' }}</td>
                        <td>{{ item.confirmed_date.strftime('%d.%m.%Y %H:%M') if item.confirmed_date else '-' }}</td>
                        <td>
                            <form method="POST" action="{{ url_for('admin_delete_repost') }}" style="display: inline;" onsubmit="return confirm('Вы уверены, что хотите удалить этот репост?');">
                                <input type="hidden" name="platform" value="{{ platform }}">
                                <input type="hidden" name="repost_id" value="{{ item.id }}">
                                <input type="hidden" name="return_page" value="{{ page }}">
                                <button type="submit" class="btn btn-danger btn-sm delete-btn">Удалить</button>
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% elif section == 'reports' %}
            <table class="table table-striped table-hover">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>ID заявителя</th>
                        <th>Канал</th>
                        <th>Причина</th>
                        <th>Дата</th>
                    </tr>
                </thead>
                <tbody>
                    {% for item in items %}
                    <tr>
                        <td>{{ item.id }}</td>
                        <td>{{ item.reporter_user_id }}</td>
                        <td>{{ item.channel_username }}</td>
                        <td>{{ item.reason }}</td>
                        <td>{{ item.report_date.strftime('%d.%m.%Y %H:%M') if item.report_date else '-' }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% endif %}
        </div>

        <!-- Pagination -->
        {% if total_pages > 1 %}
        <nav aria-label="Page navigation">
            <ul class="pagination justify-content-center">
                {% if page > 1 %}
                <li class="page-item">
                    <a class="page-link" href="{{ url_for('bot_admin', platform=platform, section=section, page=page-1, search=search) }}">Назад</a>
                </li>
                {% endif %}

                {% for p in range(1, total_pages + 1) %}
                    {% if p == page %}
                    <li class="page-item active"><span class="page-link">{{ p }}</span></li>
                    {% elif p == 1 or p == total_pages or (p >= page - 2 and p <= page + 2) %}
                    <li class="page-item"><a class="page-link" href="{{ url_for('bot_admin', platform=platform, section=section, page=p, search=search) }}">{{ p }}</a></li>
                    {% elif p == page - 3 or p == page + 3 %}
                    <li class="page-item disabled"><span class="page-link">...</span></li>
                    {% endif %}
                {% endfor %}

                {% if page < total_pages %}
                <li class="page-item">
                    <a class="page-link" href="{{ url_for('bot_admin', platform=platform, section=section, page=page+1, search=search) }}">Вперёд</a>
                </li>
                {% endif %}
            </ul>
        </nav>
        <p class="text-center text-muted">Страница {{ page }} из {{ total_pages }} (всего записей: {{ total_count }})</p>
        {% endif %}

        {% else %}
        <div class="alert alert-info">Нет данных для отображения.</div>
        {% endif %}
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/js/bootstrap.bundle.min.js" integrity="sha384-T3BcNqdY3F5V4Y3mL9A8u1a3ZC6iO6p3kfH1sF5BReGc3c0E6C9e8D2F6GRtI3Rv" crossorigin="anonymous"></script>
</body>
</html>
'''


def admin_get_vk_channels(page=1, search=''):
    """Get VK channels with pagination and search"""
    conn = VKDatabase.get_connection()
    if not conn:
        return [], 0, 0

    cursor = conn.cursor(dictionary=True)
    offset = (page - 1) * ITEMS_PER_PAGE

    if search:
        cursor.execute(
            "SELECT COUNT(*) as total FROM vk_channels WHERE channel_username LIKE %s",
            (f'%{search}%',)
        )
        total_count = cursor.fetchone()['total']

        cursor.execute(
            "SELECT * FROM vk_channels WHERE channel_username LIKE %s ORDER BY added_date DESC LIMIT %s OFFSET %s",
            (f'%{search}%', ITEMS_PER_PAGE, offset)
        )
    else:
        cursor.execute("SELECT COUNT(*) as total FROM vk_channels")
        total_count = cursor.fetchone()['total']

        cursor.execute(
            "SELECT * FROM vk_channels ORDER BY added_date DESC LIMIT %s OFFSET %s",
            (ITEMS_PER_PAGE, offset)
        )

    items = cursor.fetchall()
    cursor.close()
    conn.close()

    total_pages = math.ceil(total_count / ITEMS_PER_PAGE) if total_count > 0 else 1
    return items, total_count, total_pages


def admin_get_vk_reposts(page=1):
    """Get VK reposts with pagination"""
    conn = VKDatabase.get_connection()
    if not conn:
        return [], 0, 0

    cursor = conn.cursor(dictionary=True)
    offset = (page - 1) * ITEMS_PER_PAGE

    cursor.execute("SELECT COUNT(*) as total FROM vk_reposts")
    total_count = cursor.fetchone()['total']

    cursor.execute(
        "SELECT * FROM vk_reposts ORDER BY created_date DESC LIMIT %s OFFSET %s",
        (ITEMS_PER_PAGE, offset)
    )

    items = cursor.fetchall()
    cursor.close()
    conn.close()

    total_pages = math.ceil(total_count / ITEMS_PER_PAGE) if total_count > 0 else 1
    return items, total_count, total_pages


def admin_get_vk_reports(page=1):
    """Get VK abuse reports with pagination"""
    conn = VKDatabase.get_connection()
    if not conn:
        return [], 0, 0

    cursor = conn.cursor(dictionary=True)
    offset = (page - 1) * ITEMS_PER_PAGE

    cursor.execute("SELECT COUNT(*) as total FROM vk_abuse_reports")
    total_count = cursor.fetchone()['total']

    cursor.execute(
        "SELECT * FROM vk_abuse_reports ORDER BY report_date DESC LIMIT %s OFFSET %s",
        (ITEMS_PER_PAGE, offset)
    )

    items = cursor.fetchall()
    cursor.close()
    conn.close()

    total_pages = math.ceil(total_count / ITEMS_PER_PAGE) if total_count > 0 else 1
    return items, total_count, total_pages


def admin_get_tg_channels(page=1, search=''):
    """Get Telegram channels with pagination and search"""
    conn = TGDatabase.get_connection()
    if not conn:
        return [], 0, 0

    cursor = conn.cursor(dictionary=True)
    offset = (page - 1) * ITEMS_PER_PAGE

    if search:
        cursor.execute(
            "SELECT COUNT(*) as total FROM channels WHERE channel_username LIKE %s",
            (f'%{search}%',)
        )
        total_count = cursor.fetchone()['total']

        cursor.execute(
            "SELECT * FROM channels WHERE channel_username LIKE %s ORDER BY added_date DESC LIMIT %s OFFSET %s",
            (f'%{search}%', ITEMS_PER_PAGE, offset)
        )
    else:
        cursor.execute("SELECT COUNT(*) as total FROM channels")
        total_count = cursor.fetchone()['total']

        cursor.execute(
            "SELECT * FROM channels ORDER BY added_date DESC LIMIT %s OFFSET %s",
            (ITEMS_PER_PAGE, offset)
        )

    items = cursor.fetchall()
    cursor.close()
    conn.close()

    total_pages = math.ceil(total_count / ITEMS_PER_PAGE) if total_count > 0 else 1
    return items, total_count, total_pages


def admin_get_tg_reposts(page=1):
    """Get Telegram reposts with pagination"""
    conn = TGDatabase.get_connection()
    if not conn:
        return [], 0, 0

    cursor = conn.cursor(dictionary=True)
    offset = (page - 1) * ITEMS_PER_PAGE

    cursor.execute("SELECT COUNT(*) as total FROM reposts")
    total_count = cursor.fetchone()['total']

    cursor.execute(
        "SELECT * FROM reposts ORDER BY created_date DESC LIMIT %s OFFSET %s",
        (ITEMS_PER_PAGE, offset)
    )

    items = cursor.fetchall()
    cursor.close()
    conn.close()

    total_pages = math.ceil(total_count / ITEMS_PER_PAGE) if total_count > 0 else 1
    return items, total_count, total_pages


def admin_get_tg_reports(page=1):
    """Get Telegram abuse reports with pagination"""
    conn = TGDatabase.get_connection()
    if not conn:
        return [], 0, 0

    cursor = conn.cursor(dictionary=True)
    offset = (page - 1) * ITEMS_PER_PAGE

    cursor.execute("SELECT COUNT(*) as total FROM abuse_reports")
    total_count = cursor.fetchone()['total']

    cursor.execute(
        "SELECT * FROM abuse_reports ORDER BY report_date DESC LIMIT %s OFFSET %s",
        (ITEMS_PER_PAGE, offset)
    )

    items = cursor.fetchall()
    cursor.close()
    conn.close()

    total_pages = math.ceil(total_count / ITEMS_PER_PAGE) if total_count > 0 else 1
    return items, total_count, total_pages


@app.route('/bot_admin')
def bot_admin():
    """Admin interface main page"""
    # Check if admin password is configured
    if not ADMIN_PASSWORD:
        return "Admin interface is not configured. Please set ADMIN_PASSWORD in .env", 503

    # Check if user is logged in
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    platform = request.args.get('platform', 'telegram')
    section = request.args.get('section', 'channels')
    page = int(request.args.get('page', 1))
    search = request.args.get('search', '')

    items = []
    total_count = 0
    total_pages = 1
    error = None

    if platform == 'telegram':
        if section == 'channels':
            items, total_count, total_pages = admin_get_tg_channels(page, search)
            if not items and not search:
                error = "Не удалось подключиться к базе данных Telegram или таблица пуста."
        elif section == 'reposts':
            items, total_count, total_pages = admin_get_tg_reposts(page)
            if not items:
                error = "Не удалось подключиться к базе данных Telegram или таблица пуста."
        elif section == 'reports':
            items, total_count, total_pages = admin_get_tg_reports(page)
            if not items:
                error = "Не удалось подключиться к базе данных Telegram или таблица пуста."
    else:  # vk
        if section == 'channels':
            items, total_count, total_pages = admin_get_vk_channels(page, search)
            if not items and not search:
                error = "Не удалось подключиться к базе данных VK или таблица пуста."
        elif section == 'reposts':
            items, total_count, total_pages = admin_get_vk_reposts(page)
            if not items:
                error = "Не удалось подключиться к базе данных VK или таблица пуста."
        elif section == 'reports':
            items, total_count, total_pages = admin_get_vk_reports(page)
            if not items:
                error = "Не удалось подключиться к базе данных VK или таблица пуста."

    return render_template_string(
        get_admin_template(),
        platform=platform,
        section=section,
        page=page,
        search=search,
        items=items,
        total_count=total_count,
        total_pages=total_pages,
        error=error
    )


@app.route('/bot_admin/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page"""
    if not ADMIN_PASSWORD:
        return "Admin interface is not configured. Please set ADMIN_PASSWORD in .env", 503

    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('bot_admin'))
        else:
            error = "Неверный пароль"

    return render_template_string(get_login_template(), error=error)


@app.route('/bot_admin/logout')
def admin_logout():
    """Admin logout"""
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))


@app.route('/bot_admin/delete_channel', methods=['POST'])
def admin_delete_channel():
    """Delete a channel from the admin interface"""
    # Check if admin password is configured
    if not ADMIN_PASSWORD:
        return "Admin interface is not configured. Please set ADMIN_PASSWORD in .env", 503

    # Check if user is logged in
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    platform = request.form.get('platform', 'telegram')
    channel_id = request.form.get('channel_id')
    return_page = request.form.get('return_page', '1')
    return_search = request.form.get('return_search', '')

    if not channel_id:
        return redirect(url_for('bot_admin', platform=platform, section='channels', page=return_page, search=return_search))

    try:
        channel_id = int(channel_id)
    except (ValueError, TypeError):
        return redirect(url_for('bot_admin', platform=platform, section='channels', page=return_page, search=return_search))

    if platform == 'telegram':
        conn = TGDatabase.get_connection()
        table_name = 'channels'
    else:  # vk
        conn = VKDatabase.get_connection()
        table_name = 'vk_channels'

    if not conn:
        return redirect(url_for('bot_admin', platform=platform, section='channels', page=return_page, search=return_search))

    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM {table_name} WHERE id = %s", (channel_id,))
    conn.commit()
    cursor.close()
    conn.close()

    return redirect(url_for('bot_admin', platform=platform, section='channels', page=return_page, search=return_search))


@app.route('/bot_admin/delete_repost', methods=['POST'])
def admin_delete_repost():
    """Delete a repost from the admin interface"""
    # Check if admin password is configured
    if not ADMIN_PASSWORD:
        return "Admin interface is not configured. Please set ADMIN_PASSWORD in .env", 503

    # Check if user is logged in
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    platform = request.form.get('platform', 'telegram')
    repost_id = request.form.get('repost_id')
    return_page = request.form.get('return_page', '1')

    if not repost_id:
        return redirect(url_for('bot_admin', platform=platform, section='reposts', page=return_page))

    try:
        repost_id = int(repost_id)
    except (ValueError, TypeError):
        return redirect(url_for('bot_admin', platform=platform, section='reposts', page=return_page))

    if platform == 'telegram':
        conn = TGDatabase.get_connection()
        table_name = 'reposts'
    else:  # vk
        conn = VKDatabase.get_connection()
        table_name = 'vk_reposts'

    if not conn:
        return redirect(url_for('bot_admin', platform=platform, section='reposts', page=return_page))

    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM {table_name} WHERE id = %s", (repost_id,))
    conn.commit()
    cursor.close()
    conn.close()

    return redirect(url_for('bot_admin', platform=platform, section='reposts', page=return_page))


def remove_emoji(text):
    """Remove emojis from text"""
    emoji_pattern = re.compile("["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
        u"\U00002702-\U000027B0"
        u"\U000024C2-\U0001F251"
        "]+", flags=re.UNICODE)
    return emoji_pattern.sub(r'', text)


def vk_send_message(user_id, message, keyboard=None, attachment=None):
    """Send message to VK user"""
    # https://dev.vk.com/ru/api/api-requests
    # https://dev.vk.com/ru/method/messages.send
    if type(message) is not str:
        message = json.dumps(message)
    if not attachment and (message.startswith('photo-') or message.startswith('video-')):
        attachment = message
        message = ''

    import random
    files = {
        'message': (None, message),
        'peer_id': (None, user_id),
        'access_token': (None, VK_ACCESS_TOKEN),
        'v': (None, '5.199'),
        'random_id': (None, str(random.randint(0, 2**31)))
    }
    if attachment is not None:
        files['attachment'] = (None, attachment)
    if keyboard is not None:
        files['keyboard'] = (None, json.dumps(keyboard))

    response = requests.post('https://api.vk.ru/method/messages.send', files=files)
    return response.json() if response.status_code == 200 else None


def vk_send_buttons(user_id, buttons, message='', one_time=False, inline=False):
    """Send buttons to VK user"""
    # https://dev.vk.com/ru/api/bots/development/keyboard
    # https://dev.vk.com/ru/method/messages.send
    if buttons is None:
        return None
    keyboard = {
        'one_time': one_time,
        'inline': inline,
        'buttons': buttons
    }
    result = vk_send_message(user_id, message, keyboard)
    return result


def vk_create_buttons(data, color='primary', columns=2):
    """Create VK keyboard buttons"""
    if not data:
        return None
    buttons = []
    row = []
    for index, item in enumerate(data):
        label = item.get('name')
        command = item.get('value') or remove_emoji(label)
        row.append({
            'action': {
                'type': 'text',
                'payload': json.dumps({'command': command}),
                'label': label
            },
            'color': color
        })
        if (index + 1) % columns == 0:
            buttons.append(row)
            row = []
    if len(row) > 0:
        buttons.append(row)
    return buttons


def vk_get_group_info(group_id=None):
    """Get VK group information"""
    # https://dev.vk.com/ru/method/groups.getById
    if group_id is None:
        group_id = VK_GROUP_ID

    params = {
        'group_id': group_id,
        'fields': 'members_count',
        'access_token': VK_ACCESS_TOKEN,
        'v': '5.199'
    }
    response = requests.get('https://api.vk.ru/method/groups.getById', params=params)
    if response.status_code != 200:
        return None
    data = response.json()
    groups = data.get('response', {}).get('groups', [])
    return groups[0] if groups else None


# Command handlers
def handle_start(user_id):
    """Handle /start command"""
    text = (
        "👋 Добро пожаловать в бот обмена аудиторией!\n\n"
        "Используйте команду 'помощь' для просмотра всех команд."
    )
    vk_send_message(user_id, text)
    send_main_menu(user_id)


def handle_help(user_id):
    """Handle /help command"""
    help_text = """
📚 Справка по командам:

добавить - Добавить свою группу в каталог
мои - Показать мои группы
удалить [группа] - Удалить группу из каталога
обновить [группа] - Обновить количество подписчиков
найти [группа] - Найти похожие группы для обмена
готово [группа] [на_какой_группе] - Сообщить владельцу группы о выполненном репосте
подтвердить [своя_группа] [группа_репоста] - Подтвердить репост
список - Список групп, ожидающих подтверждения
статистика - Показать статистику бота
жалоба [группа] [причина] - Пожаловаться на группу и владельца
помощь - Показать эту справку

Как это работает:
1. Добавьте свою группу командой 'добавить'
2. Найдите похожие группы 'найти'
3. Подпишитесь и сделайте репост любого поста
4. Сообщите 'готово' после репоста
5. Владелец группы подтвердит 'подтвердить'
6. Ожидайте ответного репоста
    """
    vk_send_message(user_id, help_text)


def handle_add_channel(user_id, message_text):
    """Handle add channel command"""
    parts = message_text.split(maxsplit=1)
    if len(parts) < 2:
        vk_send_message(
            user_id,
            "❌ Укажите ID или screen_name группы.\n"
            "Пример: добавить club123456 или добавить mygroup"
        )
        return

    channel_username = parts[1].strip()

    # Get group info from VK API
    try:
        group_info = vk_get_group_info(channel_username)
        if not group_info:
            vk_send_message(
                user_id,
                f"❌ Не удалось получить информацию о группе {channel_username}.\n"
                "Проверьте правильность ID или screen_name."
            )
            return

        channel_id = str(group_info.get('id'))
        screen_name = group_info.get('screen_name', channel_username)
        member_count = group_info.get('members_count', 0)

        # Save in the database
        conn = VKDatabase.get_connection()
        if not conn:
            vk_send_message(user_id, "❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
            return

        cursor = conn.cursor()

        try:
            cursor.execute(
                "INSERT INTO vk_channels (channel_username, channel_id, owner_user_id, subscriber_count) "
                "VALUES (%s, %s, %s, %s)",
                (screen_name, channel_id, user_id, member_count)
            )
            conn.commit()

            vk_send_message(
                user_id,
                f"✅ Группа {screen_name} добавлена!\n"
                f"👥 Подписчиков: {member_count}"
            )
        except mysql.connector.IntegrityError:
            vk_send_message(
                user_id,
                f"❌ Группа {screen_name} уже добавлена в каталог."
            )
        finally:
            cursor.close()
            conn.close()

    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        vk_send_message(
            user_id,
            f"❌ Не удалось получить информацию о группе {channel_username}.\n"
            "Проверьте правильность ID или screen_name."
        )


def handle_my_channels(user_id):
    """Handle my channels command"""
    conn = VKDatabase.get_connection()
    if not conn:
        vk_send_message(user_id, "❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT channel_username, subscriber_count, added_date "
        "FROM vk_channels WHERE owner_user_id = %s ORDER BY added_date DESC",
        (user_id,)
    )

    channels = cursor.fetchall()
    cursor.close()
    conn.close()

    if not channels:
        vk_send_message(user_id, "📭 У вас нет добавленных групп.")
        return

    text = "📋 Ваши группы:\n\n"
    for ch in channels:
        text += f"• {ch['channel_username']} - 👥 {ch['subscriber_count']} подписчиков\n"

    vk_send_message(user_id, text)


def handle_delete_channel(user_id, message_text):
    """Handle delete channel command"""
    parts = message_text.split(maxsplit=1)
    if len(parts) < 2:
        vk_send_message(
            user_id,
            "❌ Укажите имя группы.\n"
            "Пример: удалить mygroup"
        )
        return

    channel_username = parts[1].strip()

    conn = VKDatabase.get_connection()
    if not conn:
        vk_send_message(user_id, "❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor()

    # Checking if the user is the owner
    cursor.execute(
        "SELECT id FROM vk_channels WHERE channel_username = %s AND owner_user_id = %s",
        (channel_username, user_id)
    )

    if not cursor.fetchone():
        vk_send_message(
            user_id,
            f"❌ Группа {channel_username} не найдена или вы не являетесь владельцем."
        )
        cursor.close()
        conn.close()
        return

    cursor.execute(
        "DELETE FROM vk_channels WHERE channel_username = %s AND owner_user_id = %s",
        (channel_username, user_id)
    )
    conn.commit()
    cursor.close()
    conn.close()

    vk_send_message(user_id, f"✅ Группа {channel_username} удалена из каталога.")


def handle_update_channel_stats(user_id, message_text):
    """Handle update channel stats command"""
    parts = message_text.split(maxsplit=1)
    if len(parts) < 2:
        vk_send_message(
            user_id,
            "❌ Укажите имя группы.\n"
            "Пример: обновить mygroup"
        )
        return

    channel_username = parts[1].strip()

    conn = VKDatabase.get_connection()
    if not conn:
        vk_send_message(user_id, "❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor(dictionary=True)

    # Checking if the user is the owner
    cursor.execute(
        "SELECT channel_id, subscriber_count FROM vk_channels WHERE channel_username = %s AND owner_user_id = %s",
        (channel_username, user_id)
    )

    channel_data = cursor.fetchone()
    if not channel_data:
        vk_send_message(
            user_id,
            f"❌ Группа {channel_username} не найдена или вы не являетесь владельцем."
        )
        cursor.close()
        conn.close()
        return

    old_count = channel_data['subscriber_count']

    # Get the current number of subscribers
    try:
        group_info = vk_get_group_info(channel_username)
        if not group_info:
            vk_send_message(
                user_id,
                f"❌ Не удалось получить информацию о группе {channel_username}."
            )
            cursor.close()
            conn.close()
            return

        new_count = group_info.get('members_count', 0)

        # Update in the database
        cursor.execute(
            "UPDATE vk_channels SET subscriber_count = %s WHERE channel_username = %s",
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

        vk_send_message(
            user_id,
            f"✅ Статистика группы {channel_username} обновлена!\n\n"
            f"👥 Было: {old_count}\n"
            f"👥 Стало: {new_count}\n"
            f"{change_text}"
        )

    except Exception as e:
        logger.error(f"Error updating channel stats: {e}")
        vk_send_message(
            user_id,
            f"❌ Не удалось получить информацию о группе {channel_username}."
        )
    finally:
        cursor.close()
        conn.close()


def handle_find_channels(user_id, message_text):
    """Handle find channels command"""
    parts = message_text.split(maxsplit=1)
    if len(parts) < 2:
        vk_send_message(
            user_id,
            "❌ Укажите имя своей группы.\n"
            "Пример: найти mygroup"
        )
        return

    channel_username = parts[1].strip()

    conn = VKDatabase.get_connection()
    if not conn:
        vk_send_message(user_id, "❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor(dictionary=True)

    # Getting subscribers to a user's channel
    cursor.execute(
        "SELECT subscriber_count FROM vk_channels WHERE channel_username = %s",
        (channel_username,)
    )

    result = cursor.fetchone()
    if not result:
        vk_send_message(
            user_id,
            f"❌ Группа {channel_username} не найдена в каталоге.\n"
            "Добавьте её командой 'добавить'"
        )
        cursor.close()
        conn.close()
        return

    target_count = result['subscriber_count']
    diff = math.ceil(max(target_count, 100) * 0.2)

    # Looking for similar channels (±20%) with repost counts
    cursor.execute(
        "SELECT c.channel_username, c.subscriber_count, "
        "(SELECT COUNT(*) FROM vk_reposts r WHERE r.to_channel = c.channel_username AND r.status = 'confirmed') as confirmed_count, "
        "(SELECT COUNT(*) FROM vk_reposts r WHERE r.to_channel = c.channel_username AND r.status = 'pending') as pending_count "
        "FROM vk_channels c "
        "WHERE c.channel_username != %s "
        "AND c.owner_user_id != %s "
        "AND c.subscriber_count BETWEEN %s AND %s "
        "ORDER BY RAND() LIMIT 10",
        (channel_username, user_id, max(target_count - diff, 0), target_count + diff)
    )

    channels = cursor.fetchall()
    cursor.close()
    conn.close()

    if not channels:
        vk_send_message(
            user_id,
            "😔 К сожалению, не найдено групп с похожей аудиторией.\n"
            "Попробуйте позже."
        )
        return

    text = f"🔍 Найдено {len(channels)} похожих групп:\n\n"
    for ch in channels:
        text += (f"• {ch['channel_username']} - 👥 {ch['subscriber_count']} подписчиков\n"
                 f"  ✅ Подтверждено: {ch['confirmed_count']} | ⏳ Ожидает: {ch['pending_count']}\n")

    text += "\n💡 Подпишитесь на группу, сделайте репост и используйте команду 'готово [группа] [на_какой_группе]'."

    vk_send_message(user_id, text)


def handle_done_repost(user_id, message_text):
    """Handle done repost command"""
    parts = message_text.split()
    if len(parts) < 3:
        vk_send_message(
            user_id,
            "❌ Укажите имя группы, для которой сделали репост, и группу, на которой был сделан репост.\n"
            "Пример: готово targetgroup yourgroup"
        )
        return

    to_channel = parts[1].strip()
    repost_channel = parts[2].strip()

    conn = VKDatabase.get_connection()
    if not conn:
        vk_send_message(user_id, "❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor(dictionary=True)

    # Getting the user's channel
    # cursor.execute(
    #     "SELECT channel_username FROM vk_channels WHERE owner_user_id = %s LIMIT 1",
    #     (user_id,)
    # )
    #
    # from_channel_result = cursor.fetchone()
    # if not from_channel_result:
    #     vk_send_message(
    #         user_id,
    #         "❌ У вас нет добавленных групп. Используйте команду 'добавить'"
    #     )
    #     cursor.close()
    #     conn.close()
    #     return
    #

    # Check that the user is the owner of their channel
    cursor.execute(
        "SELECT id FROM vk_channels WHERE channel_username = %s AND owner_user_id = %s",
        (repost_channel, user_id)
    )

    if not cursor.fetchone():
        vk_send_message(
            user_id,
            f"❌ Группа {repost_channel} не найдена или вы не являетесь её владельцем"
        )
        cursor.close()
        conn.close()
        return

    from_channel = repost_channel

    # Get the owner of the target channel
    cursor.execute(
        "SELECT owner_user_id FROM vk_channels WHERE channel_username = %s",
        (to_channel,)
    )

    to_owner_result = cursor.fetchone()
    if not to_owner_result:
        vk_send_message(
            user_id,
            f"❌ Группа {to_channel} не найдена в каталоге"
        )
        cursor.close()
        conn.close()
        return

    to_user_id = to_owner_result['owner_user_id']

    # Create a repost entry
    try:
        cursor.execute(
            "INSERT INTO vk_reposts (from_channel, to_channel, repost_channel, from_user_id, to_user_id, status) "
            "VALUES (%s, %s, %s, %s, %s, 'pending')",
            (from_channel, to_channel, repost_channel, user_id, to_user_id)
        )
        conn.commit()

        vk_send_message(
            user_id,
            f"✅ Уведомление отправлено владельцу группы {to_channel}.\n"
            "Ожидайте подтверждения."
        )

        # Notify the channel owner
        try:
            vk_send_message(
                to_user_id,
                f"🔔 Новое уведомление о репосте!\n\n"
                f"Группа {repost_channel} сообщает, что сделала репост для {to_channel}.\n\n"
                f"Проверьте и подтвердите командой:\n"
                f"подтвердить {to_channel} {repost_channel}"
            )
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    except mysql.connector.IntegrityError:
        vk_send_message(
            user_id,
            "❌ Запрос на подтверждение репоста уже существует"
        )
    finally:
        cursor.close()
        conn.close()


def handle_confirm_repost(user_id, message_text):
    """Handle confirm repost command"""
    parts = message_text.split()
    if len(parts) < 3:
        vk_send_message(
            user_id,
            "❌ Укажите имя своей группы и группу, на которой сделан репост.\n"
            "Пример: подтвердить mygroup repost_group"
        )
        return

    my_channel = parts[1].strip()
    repost_channel = parts[2].strip()

    conn = VKDatabase.get_connection()
    if not conn:
        vk_send_message(user_id, "❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor(dictionary=True)

    # Check that the user is the owner of their channel
    cursor.execute(
        "SELECT id FROM vk_channels WHERE channel_username = %s AND owner_user_id = %s",
        (my_channel, user_id)
    )

    if not cursor.fetchone():
        vk_send_message(
            user_id,
            f"❌ Группа {my_channel} не найдена или вы не являетесь её владельцем"
        )
        cursor.close()
        conn.close()
        return

    # Finding a pending repost
    cursor.execute(
        "SELECT r.id, r.from_channel, r.from_user_id "
        "FROM vk_reposts r "
        "WHERE r.to_channel = %s AND r.from_channel = %s AND r.to_user_id = %s AND r.status = 'pending' "
        "LIMIT 1",
        (my_channel, repost_channel, user_id)
    )

    repost = cursor.fetchone()
    if not repost:
        vk_send_message(
            user_id,
            f"❌ Нет ожидающих подтверждения репостов от группы {repost_channel} для {my_channel}."
        )
        cursor.close()
        conn.close()
        return

    # Updating the subscriber count on both channels
    updated_counts = {}

    # Updating the subscribers of the channel that reposted
    try:
        repost_group_info = vk_get_group_info(repost_channel)
        if repost_group_info:
            repost_member_count = repost_group_info.get('members_count', 0)
            cursor.execute(
                "UPDATE vk_channels SET subscriber_count = %s WHERE channel_username = %s",
                (repost_member_count, repost_channel)
            )
            updated_counts[repost_channel] = repost_member_count
    except Exception as e:
        logger.error(f"Failed to update subscriber count for {repost_channel}: {e}")

    # Updating your channel's subscribers
    try:
        my_group_info = vk_get_group_info(my_channel)
        if my_group_info:
            my_member_count = my_group_info.get('members_count', 0)
            cursor.execute(
                "UPDATE vk_channels SET subscriber_count = %s WHERE channel_username = %s",
                (my_member_count, my_channel)
            )
            updated_counts[my_channel] = my_member_count
    except Exception as e:
        logger.error(f"Failed to update subscriber count for {my_channel}: {e}")

    # Confirming the repost
    cursor.execute(
        "UPDATE vk_reposts SET status = 'confirmed', confirmed_date = NOW() WHERE id = %s",
        (repost['id'],)
    )
    conn.commit()
    cursor.close()
    conn.close()

    response_text = f"✅ Репост от группы {repost_channel} для вашей группы {my_channel} подтверждён!"
    if updated_counts:
        response_text += "\n\n📊 Обновлена статистика:"
        for channel, count in updated_counts.items():
            response_text += f"\n• {channel}: {count} подписчиков"

    vk_send_message(user_id, response_text)

    # Notify the author of the repost
    try:
        notification_text = (
            f"🎉 Ваш репост подтверждён!\n\n"
            f"Владелец группы {my_channel} подтвердил репост с вашей группы {repost_channel}."
        )
        if updated_counts:
            notification_text += "\n\n📊 Обновлена статистика:"
            for channel, count in updated_counts.items():
                notification_text += f"\n• {channel}: {count} подписчиков"

        vk_send_message(repost['from_user_id'], notification_text)
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")


def handle_list_pending(user_id):
    """Handle list pending command"""
    conn = VKDatabase.get_connection()
    if not conn:
        vk_send_message(user_id, "❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT r.from_channel, r.to_channel, r.created_date "
        "FROM vk_reposts r "
        "WHERE r.to_user_id = %s AND r.status = 'pending' "
        "ORDER BY r.created_date DESC",
        (user_id,)
    )

    reposts = cursor.fetchall()
    cursor.close()
    conn.close()

    if not reposts:
        vk_send_message(user_id, "📭 Нет ожидающих подтверждения репостов.")
        return

    text = "📋 Ожидают подтверждения:\n\n"
    for r in reposts:
        date_str = r['created_date'].strftime('%d.%m.%Y %H:%M')
        text += f"• {r['from_channel']} → {r['to_channel']}\n  📅 {date_str}\n\n"

    text += "Используйте 'подтвердить [своя_группа] [группа_репоста]' для подтверждения."

    vk_send_message(user_id, text)


def handle_show_statistics(user_id):
    """Handle show statistics command"""
    conn = VKDatabase.get_connection()
    if not conn:
        vk_send_message(user_id, "❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor(dictionary=True)

    # Get total number of channels
    cursor.execute("SELECT COUNT(*) as total FROM vk_channels")
    channels_count = cursor.fetchone()['total']

    # Get total number of confirmed reposts
    cursor.execute("SELECT COUNT(*) as total FROM vk_reposts WHERE status = 'confirmed'")
    confirmed_count = cursor.fetchone()['total']

    # Get total number of pending reposts
    cursor.execute("SELECT COUNT(*) as total FROM vk_reposts WHERE status = 'pending'")
    pending_count = cursor.fetchone()['total']

    cursor.close()
    conn.close()

    text = (
        "📊 Статистика бота:\n\n"
        f"📺 Всего групп: {channels_count}\n"
        f"✅ Подтверждённых репостов: {confirmed_count}\n"
        f"⏳ Ожидают подтверждения: {pending_count}"
    )

    vk_send_message(user_id, text)


def handle_report_abuse(user_id, message_text):
    """Handle report abuse command"""
    parts = message_text.split(maxsplit=2)
    if len(parts) < 3:
        vk_send_message(
            user_id,
            "❌ Укажите группу и причину жалобы.\n"
            "Пример: жалоба badgroup Не делает репосты"
        )
        return

    channel_username = parts[1].strip()
    reason = parts[2].strip()

    conn = VKDatabase.get_connection()
    if not conn:
        vk_send_message(user_id, "❌ Ошибка. Пожалуйста, попробуйте повторить попытку позже.")
        return

    cursor = conn.cursor()

    # Checking the existence of the channel
    cursor.execute(
        "SELECT id, owner_user_id FROM vk_channels WHERE channel_username = %s",
        (channel_username,)
    )

    target_channel = cursor.fetchone()
    if not target_channel:
        vk_send_message(
            user_id,
            f"❌ Группа {channel_username} не найдена в каталоге."
        )
        cursor.close()
        conn.close()
        return

    if user_id == target_channel[1]:
        vk_send_message(
            user_id,
            "❌ Вы не можете пожаловаться на свою группу."
        )
        cursor.close()
        conn.close()
        return

    # Saving the complaint
    cursor.execute(
        "INSERT INTO vk_abuse_reports (reporter_user_id, channel_username, reason) "
        "VALUES (%s, %s, %s)",
        (user_id, channel_username, reason)
    )
    conn.commit()
    cursor.close()
    conn.close()

    vk_send_message(
        user_id,
        f"✅ Жалоба на группу {channel_username} зарегистрирована.\n"
        "Спасибо за информацию!"
    )


def send_main_menu(user_id):
    """Send main menu buttons to user"""
    buttons_data = [
        {'name': '➕ Добавить группу', 'value': 'добавить'},
        {'name': '📋 Мои группы', 'value': 'мои'},
        {'name': '🔍 Найти группы', 'value': 'найти_помощь'},
        {'name': '✅ Готово', 'value': 'готово_помощь'},
        {'name': '📊 Статистика', 'value': 'статистика'},
        {'name': 'ℹ️ Помощь', 'value': 'помощь'},
    ]
    buttons = vk_create_buttons(buttons_data, columns=2)
    vk_send_buttons(user_id, buttons, "Выберите действие:")


@app.route('/vk_callback', methods=['POST'])
def vk_callback():
    """Handle VK Callback API requests"""
    try:
        data = request.get_json()
    except Exception:
        return 'fail'

    if not data:
        return 'fail'

    # Debug logging
    logger.debug(json.dumps(data, indent=4, ensure_ascii=False))

    event_type = data.get('type')
    group_id = data.get('group_id')

    # Confirmation
    if event_type == 'confirmation':
        logger.info(f"Confirmation request from group {group_id}")
        return VK_CONFIRMATION_CODE

    # Handle message_new event
    if event_type == 'message_new':
        message = data.get('object', {}).get('message', {})

        message_user_id = message.get('from_id')
        message_payload = message.get('payload')
        message_payload = json.loads(message_payload) if message_payload else None
        command_text = message_payload.get('command') if message_payload else ''
        message_text = message.get('text', '')
        message_text = remove_emoji(message_text).strip()

        logger.info(f"Message from user {message_user_id}: {message_text}")
        logger.debug(f"Command: {command_text}")

        # Handle commands from buttons (payload)
        if command_text:
            message_text = command_text

        # Normalize message text to lowercase for command matching
        message_text_lower = message_text.lower()

        # Handle start command
        if message_text_lower in ['начать', 'start', 'старт']:
            handle_start(message_user_id)

        # Handle help command
        elif message_text_lower in ['помощь', 'help', 'справка']:
            handle_help(message_user_id)

        # Handle add command
        elif message_text_lower.startswith('добавить'):
            handle_add_channel(message_user_id, message_text_lower)

        # Handle my channels command
        elif message_text_lower in ['мои', 'мои группы']:
            handle_my_channels(message_user_id)

        # Handle delete command
        elif message_text_lower.startswith('удалить'):
            handle_delete_channel(message_user_id, message_text_lower)

        # Handle update command
        elif message_text_lower.startswith('обновить'):
            handle_update_channel_stats(message_user_id, message_text_lower)

        # Handle find command
        elif message_text_lower.startswith('найти'):
            if message_text_lower == 'найти_помощь':
                vk_send_message(
                    message_user_id,
                    "Для поиска групп используйте команду:\nнайти [имя_вашей_группы]\n\nПример: найти mygroup"
                )
            else:
                handle_find_channels(message_user_id, message_text_lower)

        # Handle done command
        elif message_text_lower.startswith('готово'):
            if message_text_lower == 'готово_помощь':
                vk_send_message(
                    message_user_id,
                    "Для отправки уведомления о репосте используйте команду:\nготово [имя_группы] [на_какой_группе]\n\nПример: готово targetgroup yourgroup"
                )
            else:
                handle_done_repost(message_user_id, message_text_lower)

        # Handle confirm command
        elif message_text_lower.startswith('подтвердить'):
            handle_confirm_repost(message_user_id, message_text_lower)

        # Handle list command
        elif message_text_lower in ['список', 'ожидают']:
            handle_list_pending(message_user_id)

        # Handle statistics command
        elif message_text_lower in ['статистика', 'стат', 'stat']:
            handle_show_statistics(message_user_id)

        # Handle abuse command
        elif message_text_lower.startswith('жалоба'):
            handle_report_abuse(message_user_id, message_text_lower)

        # Unknown command
        else:
            vk_send_message(
                message_user_id,
                "❓ Неизвестная команда. Используйте 'помощь' для просмотра списка команд."
            )
            send_main_menu(message_user_id)

        return 'ok'

    return 'ok'


def main():
    # Database initialization
    VKDatabase.init_db()

    logger.info(f"Starting VK bot on {VK_FLASK_HOST}:{VK_FLASK_PORT}")
    app.run(host=VK_FLASK_HOST, port=VK_FLASK_PORT, debug=False)


if __name__ == '__main__':
    main()
