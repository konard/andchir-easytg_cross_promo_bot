# A Telegram bot for mutual reposts and audience sharing

*Справка по командам:*

**/add** - Добавить свой канал в каталог
**/my** - Показать мои каналы
**/delete** *[канал]* - Удалить канал из каталога
**/update** *[канал]* - Обновить количество подписчиков
**/find** *[канал]* - Найти похожие каналы для обмена
**/done** *[канал]* - Сообщить владельцу канала о выполненном репосте
**/confirm** *[свой_канал]* *[канал_репоста]* - Подтвердить репост
**/list** - Список каналов, ожидающих подтверждения
**/stat** - Показать статистику бота
**/abuse** *[канал]* *[причина]* - Пожаловаться на канал и владельца
**/help** - Показать эту справку

*Как это работает:*
1. Добавьте свой канал командой **/add**
2. Найдите похожие каналы **/find**
3. Подпишитесь и сделайте репост любого поста
4. Сообщите **/done** после репоста
5. Владелец канала подтвердит **/confirm**
6. Ожидайте ответного репоста

## Deploy

~~~
sudo nano /etc/systemd/system/easytg_cross_promo_bot.service
~~~

~~~
sudo systemctl daemon-reload
sudo systemctl enable easytg_cross_promo_bot
sudo systemctl start easytg_cross_promo_bot
sudo systemctl status easytg_cross_promo_bot
~~~

## Режим работы

Бот поддерживает два режима работы:

### Polling (по умолчанию)

Бот периодически опрашивает сервер Telegram на наличие новых сообщений.

```
BOT_MODE=polling
```

### Webhook (рекомендуется для продакшена)

Для более быстрой работы можно использовать режим webhook. В этом режиме бот создает веб-сервер, на который Telegram отправляет обновления.

```
BOT_MODE=webhook
WEBHOOK_URL=https://example.com:8443
WEBHOOK_PORT=8443
WEBHOOK_SECRET_TOKEN=your_secret_token
```

**Требования для webhook:**
- Публичный IP адрес или домен с HTTPS
- Telegram поддерживает только порты: 443, 80, 88, 8443

**Опциональные параметры для прямого подключения (без reverse proxy):**
```
WEBHOOK_CERT=/path/to/cert.pem
WEBHOOK_KEY=/path/to/private.key
```

При использовании reverse proxy (nginx, haproxy) сертификаты настраиваются на стороне прокси.
