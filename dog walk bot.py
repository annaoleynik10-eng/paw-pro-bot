"""
Бот выгула собак — полная версия
"""

import logging
import json
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8881158025:AAEWRWBgjm7f4CRnKFUVyzck7NeEqxHh0ZQ"
MANAGER_USERNAME = "lon_neli"  # первый менеджер — определяется по нику

CLIENTS_FILE  = "clients.json"
ORDERS_FILE   = "orders.json"
WALKERS_FILE  = "walkers.json"
MANAGERS_FILE = "managers.json"

TARIFFS = {
    "30min":     {"label": "30 мин",                "price": 335},
    "60min":     {"label": "60 мин",                "price": 495},
    "extra_dog": {"label": "Доп. собака",            "price": 200},
    "wash_paws": {"label": "Помыть лапы",            "price": 100},
    "night":     {"label": "До 8:00 / после 21:00", "price": 200},
}

# ─── ХРАНИЛИЩЕ ───────────────────────────────────────────────────────────────

def load(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ─── РОЛИ ────────────────────────────────────────────────────────────────────

def get_managers():
    return load(MANAGERS_FILE)

def get_walkers():
    return load(WALKERS_FILE)

def is_manager(user):
    managers = get_managers()
    uid = str(user.id)
    if uid in managers:
        return True
    if user.username and user.username.lower() == MANAGER_USERNAME.lower():
        managers[uid] = {"name": user.full_name, "username": user.username or ""}
        save(MANAGERS_FILE, managers)
        return True
    return False

def register_walker(user):
    walkers = get_walkers()
    uid = str(user.id)
    if uid not in walkers:
        walkers[uid] = {"name": user.full_name, "username": user.username or ""}
        save(WALKERS_FILE, walkers)
        return True
    return False

# ─── СТАТИСТИКА ──────────────────────────────────────────────────────────────

def dog_stats(client_id):
    """Возвращает {walker_id: {"name": ..., "count": N, "dates": [...]}}"""
    orders = load(ORDERS_FILE)
    stats = {}
    for o in orders.values():
        if o.get("client_id") != client_id:
            continue
        if o.get("status") != "done":
            continue
        wid = str(o.get("walker_id", ""))
        if not wid:
            continue
        if wid not in stats:
            stats[wid] = {"name": o.get("walker", "?"), "count": 0, "dates": []}
        stats[wid]["count"] += 1
        stats[wid]["dates"].append(o.get("datetime", ""))
    return stats

def suggest_walker(client_id):
    """Предлагает знакомого выгульщика если у него 10+ выгулов этой собаки."""
    stats = dog_stats(client_id)
    if not stats:
        return None
    best = max(stats.values(), key=lambda x: x["count"])
    best_id = [k for k, v in stats.items() if v == best][0]
    if best["count"] >= 10:
        return {"walker_id": best_id, "name": best["name"], "count": best["count"]}
    return None

# ─── СОСТОЯНИЯ ───────────────────────────────────────────────────────────────

ADD_NAME, ADD_ADDRESS, ADD_DOG = range(3)
SEL_CLIENT, SEL_DURATION, SEL_OPTIONS, ENTER_DT, SEL_WALKER = range(10, 15)
ADD_MANAGER_WAIT = 20

# ─── /start ──────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_manager(user):
        await update.message.reply_text(
            f"👋 Привет, {user.first_name}! Ты менеджер.\n\n"
            "/neworder — создать заявку\n"
            "/addclient — добавить клиента\n"
            "/clients — список клиентов\n"
            "/walkers — список выгульщиков\n"
            "/addmanager — добавить менеджера\n"
            "/cancel — отменить"
        )
        return

    is_new = register_walker(user)
    if is_new:
        await update.message.reply_text(
            f"🐕 Привет, {user.first_name}!\n"
            "Ты зарегистрирован как выгульщик.\n"
            "Здесь будут появляться заявки на выгул."
        )
    else:
        await update.message.reply_text(f"🐕 Привет, {user.first_name}! Ждём заявок.")

# ─── МЕНЕДЖЕРЫ ───────────────────────────────────────────────────────────────

async def add_manager_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user):
        await update.message.reply_text("❌ Нет доступа.")
        return ConversationHandler.END
    await update.message.reply_text(
        "Перешли мне любое сообщение от нового менеджера\n"
        "_(или попроси его написать боту /start и сообщи мне его @username)_\n\n"
        "Или введи @username вручную:",
        parse_mode="Markdown"
    )
    return ADD_MANAGER_WAIT

async def add_manager_by_username(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Если переслали сообщение
    if update.message.forward_origin:
        fwd = update.message.forward_origin
        if hasattr(fwd, "sender_user") and fwd.sender_user:
            u = fwd.sender_user
            managers = get_managers()
            uid = str(u.id)
            managers[uid] = {"name": u.full_name, "username": u.username or ""}
            save(MANAGERS_FILE, managers)
            await update.message.reply_text(f"✅ {u.full_name} добавлен как менеджер.")
            return ConversationHandler.END

    # Если ввели @username вручную
    text = update.message.text.strip().lstrip("@")
    managers = get_managers()
    # Ищем в walkers по username
    walkers = get_walkers()
    found = None
    for wid, w in walkers.items():
        if w.get("username", "").lower() == text.lower():
            found = (wid, w)
            break
    if found:
        wid, w = found
        managers[wid] = {"name": w["name"], "username": w.get("username", "")}
        save(MANAGERS_FILE, managers)
        await update.message.reply_text(f"✅ @{text} ({w['name']}) добавлен как менеджер.")
    else:
        await update.message.reply_text(
            f"⚠️ @{text} не найден среди зарегистрированных пользователей.\n"
            "Попроси его сначала написать /start боту."
        )
    return ConversationHandler.END

async def list_walkers(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user): return
    walkers = get_walkers()
    managers = get_managers()
    if not walkers:
        await update.message.reply_text("Выгульщиков нет. Пусть напишут /start.")
        return
    text = f"🐕 *Выгульщики ({len(walkers)}):*\n\n"
    for wid, w in walkers.items():
        un = f"@{w['username']}" if w.get("username") else ""
        role = " 👔" if wid in managers else ""
        text += f"• {w['name']} {un}{role}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# ─── КЛИЕНТЫ ─────────────────────────────────────────────────────────────────

async def add_client_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user):
        return ConversationHandler.END
    await update.message.reply_text("Имя клиента:")
    return ADD_NAME

async def add_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["nc"] = {"name": update.message.text.strip()}
    await update.message.reply_text("Адрес (улица, дом, кв):")
    return ADD_ADDRESS

async def add_address(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["nc"]["address"] = update.message.text.strip()
    await update.message.reply_text("Кличка и порода:\n(например: Барсик, лабрадор)")
    return ADD_DOG

async def add_dog(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    clients = load(CLIENTS_FILE)
    c = ctx.user_data["nc"]
    c["dog"] = update.message.text.strip()
    cid = str(len(clients) + 1)
    clients[cid] = c
    save(CLIENTS_FILE, clients)
    await update.message.reply_text(
        f"✅ Клиент добавлен!\n\n👤 {c['name']}\n📍 {c['address']}\n🐕 {c['dog']}"
    )
    return ConversationHandler.END

async def list_clients(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user): return
    clients = load(CLIENTS_FILE)
    if not clients:
        await update.message.reply_text("Клиентов нет. /addclient")
        return
    text = "📋 *Клиенты:*\n\n"
    for cid, c in clients.items():
        stats = dog_stats(cid)
        total_walks = sum(v["count"] for v in stats.values())
        text += f"*{c['name']}* — {c['dog']} (выгулов: {total_walks})\n📍 {c['address']}\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# ─── СОЗДАНИЕ ЗАЯВКИ ─────────────────────────────────────────────────────────

async def new_order_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.effective_user):
        return ConversationHandler.END
    clients = load(CLIENTS_FILE)
    if not clients:
        await update.message.reply_text("Нет клиентов. /addclient")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"{c['name']} — {c['dog']}", callback_data=f"sc:{cid}")]
          for cid, c in clients.items()]
    await update.message.reply_text("Выберите клиента:", reply_markup=InlineKeyboardMarkup(kb))
    return SEL_CLIENT

async def sel_client(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    cid = q.data.split(":")[1]
    clients = load(CLIENTS_FILE)
    ctx.user_data["order"] = {"client_id": cid, "client": clients[cid], "options": [], "responses": {}}

    # Показываем статистику по собаке
    stats = dog_stats(cid)
    c = clients[cid]
    stat_text = ""
    if stats:
        stat_text = "\n\n📊 *История выгулов:*\n"
        for v in sorted(stats.values(), key=lambda x: -x["count"]):
            stat_text += f"• {v['name']} — {v['count']} раз\n"

    kb = [
        [InlineKeyboardButton("30 мин — 335₽", callback_data="dur:30min")],
        [InlineKeyboardButton("60 мин — 495₽", callback_data="dur:60min")],
    ]
    await q.edit_message_text(
        f"👤 {c['name']} | 🐕 {c['dog']}\n📍 {c['address']}{stat_text}\n\nДлительность:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return SEL_DURATION

async def sel_duration(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data["order"]["duration"] = q.data.split(":")[1]
    ctx.user_data["order"]["options"] = []
    await show_options(q, ctx)
    return SEL_OPTIONS

def options_kb(selected):
    opts = ["extra_dog", "wash_paws", "night"]
    kb = []
    for key in opts:
        t = TARIFFS[key]
        check = "✅ " if key in selected else ""
        kb.append([InlineKeyboardButton(f"{check}{t['label']} +{t['price']}₽",
                                        callback_data=f"opt:{key}")])
    kb.append([InlineKeyboardButton("➡️ Далее — ввести время", callback_data="opt:done")])
    return InlineKeyboardMarkup(kb)

def calc_total(order):
    total = TARIFFS[order["duration"]]["price"]
    for opt in order.get("options", []):
        total += TARIFFS[opt]["price"]
    return total

async def show_options(q, ctx):
    order = ctx.user_data["order"]
    dur = TARIFFS[order["duration"]]
    total = calc_total(order)
    c = order["client"]
    await q.edit_message_text(
        f"👤 {c['name']} | 🐕 {c['dog']}\n\n"
        f"⏱ {dur['label']} — {dur['price']}₽\n"
        f"💰 Итого: *{total}₽*\n\nДоп. услуги:",
        parse_mode="Markdown",
        reply_markup=options_kb(order["options"])
    )

async def sel_option(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    key = q.data.split(":")[1]
    if key == "done":
        order = ctx.user_data["order"]
        c = order["client"]
        await q.edit_message_text(
            f"👤 {c['name']} | 🐕 {c['dog']}\n📍 {c['address']}\n"
            f"💰 Сумма: *{calc_total(order)}₽*\n\n"
            "Введите дату и время:\n_(например: 25.06 14:30)_",
            parse_mode="Markdown"
        )
        return ENTER_DT
    opts = ctx.user_data["order"]["options"]
    if key in opts:
        opts.remove(key)
    else:
        opts.append(key)
    await show_options(q, ctx)
    return SEL_OPTIONS

async def enter_dt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    dt_text = update.message.text.strip()
    order = ctx.user_data["order"]
    order["datetime"] = dt_text
    order["responses"] = {}  # walker_id -> {name, time}
    manager = update.effective_user

    orders = load(ORDERS_FILE)
    oid = str(len(orders) + 1)
    ctx.user_data["order_id"] = oid

    dur = TARIFFS[order["duration"]]
    extras = [TARIFFS[k]["label"] for k in order.get("options", [])]
    total = calc_total(order)
    c = order["client"]
    extras_line = ("\n➕ " + ", ".join(extras)) if extras else ""

    card = (
        f"🐕 *Новая заявка #{oid}*\n\n"
        f"👤 {c['name']}\n"
        f"🏠 {c['address']}\n"
        f"🐶 {c['dog']}\n"
        f"🕐 {dt_text}\n"
        f"⏱ {dur['label']}{extras_line}\n"
        f"💰 *{total}₽*\n\n"
        "Нажми кнопку если можешь выйти:"
    )
    kb = [[InlineKeyboardButton("✋ Откликнуться", callback_data=f"respond:{oid}")]]

    walkers = get_walkers()
    sent_to = []
    for wid, w in walkers.items():
        try:
            msg = await update.get_bot().send_message(
                chat_id=int(wid), text=card,
                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
            )
            sent_to.append({"walker_id": wid, "message_id": msg.message_id})
        except Exception as e:
            logger.warning(f"Не смог отправить {wid}: {e}")

    orders[oid] = {
        **order,
        "manager_id": manager.id,
        "manager_name": manager.full_name,
        "status": "open",        # open → assigned → done
        "walker": None,
        "walker_id": None,
        "total": total,
        "sent_to": sent_to,
        "responses": {},          # walker_id -> {name, responded_at}
        "created_at": datetime.now().isoformat(),
    }
    save(ORDERS_FILE, orders)

    # Проверяем — есть ли знакомый выгульщик (10+ выгулов)
    suggestion = suggest_walker(order["client_id"])
    suggestion_text = ""
    if suggestion:
        suggestion_text = (
            f"\n\n💡 *Подсказка:* {suggestion['name']} уже выгуливал эту собаку "
            f"{suggestion['count']} раз — рекомендую назначить его."
        )

    await update.message.reply_text(
        f"✅ Заявка #{oid} отправлена {len(sent_to)} выгульщикам!\n"
        f"💰 Сумма: *{total}₽*{suggestion_text}\n\n"
        "Жду откликов. Когда будут ответы — пришлю список для выбора исполнителя.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ─── ОТКЛИК ВЫГУЛЬЩИКА ───────────────────────────────────────────────────────

async def walker_respond(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    oid = q.data.split(":")[1]
    orders = load(ORDERS_FILE)

    if oid not in orders:
        await q.answer("Заявка не найдена.", show_alert=True)
        return

    order = orders[oid]
    if order["status"] != "open":
        await q.answer("Эта заявка уже закрыта.", show_alert=True)
        return

    walker = q.from_user
    wid = str(walker.id)

    if wid in order["responses"]:
        await q.answer("Ты уже откликнулся! Ждём решения менеджера.", show_alert=True)
        return

    # Фиксируем отклик с временем
    now = datetime.now().strftime("%H:%M:%S")
    order["responses"][wid] = {
        "name": walker.full_name,
        "username": walker.username or "",
        "responded_at": now,
    }
    save(ORDERS_FILE, orders)

    # Подтверждаем выгульщику
    await q.edit_message_text(
        q.message.text + f"\n\n✋ Ты откликнулся в {now}. Ждём решения менеджера.",
        parse_mode="Markdown"
    )
    await q.answer("Отклик принят! Ждём решения менеджера.")

    # Уведомляем менеджера с кнопками выбора
    await notify_manager_responses(ctx.bot, oid, order)

async def notify_manager_responses(bot, oid, order):
    """Отправляет/обновляет менеджеру список откликнувшихся."""
    responses = order["responses"]
    c = order["client"]
    clients = load(CLIENTS_FILE)

    # Статистика по этой собаке
    stats = dog_stats(order["client_id"])

    text = (
        f"📋 *Заявка #{oid}* — {c['name']}, {c['dog']}\n"
        f"🕐 {order['datetime']} | 💰 {order['total']}₽\n\n"
        f"*Откликнулись ({len(responses)}):*\n"
    )
    kb = []
    for wid, r in responses.items():
        walk_count = stats.get(wid, {}).get("count", 0)
        familiar = " ⭐" if walk_count >= 10 else (f" ({walk_count} выг.)" if walk_count > 0 else " (новый)")
        text += f"• {r['name']}{familiar} — в {r['responded_at']}\n"
        kb.append([InlineKeyboardButton(
            f"Назначить: {r['name']}{familiar}",
            callback_data=f"assign:{oid}:{wid}"
        )])

    # Проверяем знакомого
    suggestion = suggest_walker(order["client_id"])
    if suggestion and suggestion["walker_id"] in responses:
        text += f"\n💡 Рекомендую {suggestion['name']} — {suggestion['count']} выгулов этой собаки"

    try:
        # Пробуем обновить предыдущее сообщение
        prev_msg_id = order.get("manager_msg_id")
        if prev_msg_id:
            await bot.edit_message_text(
                chat_id=order["manager_id"],
                message_id=prev_msg_id,
                text=text, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb)
            )
        else:
            msg = await bot.send_message(
                chat_id=order["manager_id"],
                text=text, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            # Сохраняем ID сообщения для обновления
            orders = load(ORDERS_FILE)
            if oid in orders:
                orders[oid]["manager_msg_id"] = msg.message_id
                save(ORDERS_FILE, orders)
    except Exception as e:
        logger.warning(f"Ошибка уведомления менеджера: {e}")
        try:
            msg = await bot.send_message(
                chat_id=order["manager_id"],
                text=text, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            orders = load(ORDERS_FILE)
            if oid in orders:
                orders[oid]["manager_msg_id"] = msg.message_id
                save(ORDERS_FILE, orders)
        except Exception:
            pass

# ─── МЕНЕДЖЕР НАЗНАЧАЕТ ИСПОЛНИТЕЛЯ ─────────────────────────────────────────

async def assign_walker(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    parts = q.data.split(":")
    oid, wid = parts[1], parts[2]
    orders = load(ORDERS_FILE)

    if oid not in orders:
        await q.edit_message_text("Заявка не найдена.")
        return

    order = orders[oid]
    if order["status"] != "open":
        await q.edit_message_text("Заявка уже назначена.")
        return

    response = order["responses"].get(wid)
    if not response:
        await q.edit_message_text("Этот выгульщик не откликался.")
        return

    walker_name = response["name"]
    c = order["client"]
    dur = TARIFFS[order["duration"]]
    extras = [TARIFFS[k]["label"] for k in order.get("options", [])]
    extras_line = ("\n➕ " + ", ".join(extras)) if extras else ""

    # Обновляем заказ
    orders[oid]["status"] = "done"
    orders[oid]["walker"] = walker_name
    orders[oid]["walker_id"] = int(wid)
    save(ORDERS_FILE, orders)

    # Сообщение назначенному выгульщику
    assigned_card = (
        f"✅ *Ты назначен на заявку #{oid}!*\n\n"
        f"👤 {c['name']}\n"
        f"🏠 {c['address']}\n"
        f"🐶 {c['dog']}\n"
        f"🕐 {order['datetime']}\n"
        f"⏱ {dur['label']}{extras_line}\n"
        f"💰 *{order['total']}₽*"
    )
    try:
        await ctx.bot.send_message(
            chat_id=int(wid), text=assigned_card, parse_mode="Markdown"
        )
    except Exception:
        pass

    # Остальным — отбой
    closed_card = (
        f"🔒 Заявка #{oid} ({c['dog']}, {order['datetime']}) — "
        f"назначен другой выгульщик. Спасибо за отклик!"
    )
    for entry in order.get("sent_to", []):
        ewid = entry["walker_id"]
        if str(ewid) == str(wid):
            continue
        try:
            await ctx.bot.send_message(chat_id=int(ewid), text=closed_card)
        except Exception:
            pass

    # Обновляем сообщение менеджера
    await q.edit_message_text(
        f"✅ *Заявка #{oid} назначена*\n\n"
        f"🐕 {c['dog']} ({c['name']})\n"
        f"🕐 {order['datetime']}\n"
        f"👤 Исполнитель: *{walker_name}*\n"
        f"💰 {order['total']}₽",
        parse_mode="Markdown"
    )

# ─── ОТМЕНА ──────────────────────────────────────────────────────────────────

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

# ─── ЗАПУСК ──────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("addclient", add_client_start)],
        states={
            ADD_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            ADD_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_address)],
            ADD_DOG:     [MessageHandler(filters.TEXT & ~filters.COMMAND, add_dog)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    order_conv = ConversationHandler(
        entry_points=[CommandHandler("neworder", new_order_start)],
        states={
            SEL_CLIENT:   [CallbackQueryHandler(sel_client,   pattern="^sc:")],
            SEL_DURATION: [CallbackQueryHandler(sel_duration, pattern="^dur:")],
            SEL_OPTIONS:  [CallbackQueryHandler(sel_option,   pattern="^opt:")],
            ENTER_DT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_dt)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    add_manager_conv = ConversationHandler(
        entry_points=[CommandHandler("addmanager", add_manager_start)],
        states={
            ADD_MANAGER_WAIT: [
                MessageHandler(filters.FORWARDED, add_manager_by_username),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_manager_by_username),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("clients", list_clients))
    app.add_handler(CommandHandler("walkers", list_walkers))
    app.add_handler(add_conv)
    app.add_handler(order_conv)
    app.add_handler(add_manager_conv)
    app.add_handler(CallbackQueryHandler(walker_respond, pattern="^respond:"))
    app.add_handler(CallbackQueryHandler(assign_walker,  pattern="^assign:"))

    logger.info("Бот запущен 🐕")
    app.run_polling()

if __name__ == "__main__":
    main()
