import asyncio
import logging
import json
import os
import threading
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from flask import Flask

import os

# --- НАСТРОЙКИ ---
# Больше не записывайте сюда токен, читайте из переменных окружения
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
USER_TWO_ID = int(os.environ.get("USER_TWO_ID", 0))

# ... остальной код без изменений ...
# Файл данных будет создан в папке с проектом
DATA_FILE = "data.json"

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- FLASK ДЛЯ RENDER (чтобы сервис не падал) ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running", 200

# --- РАБОТА С JSON ---
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"shifts": {}, "notes": {}}

def save_data(data):
    tmp_file = DATA_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, DATA_FILE)

# --- МАШИНА СОСТОЯНИЙ ---
class Form(StatesGroup):
    waiting_for_date = State()
    waiting_for_shift_type = State()
    waiting_for_note_text = State()

# --- КЛАВИАТУРЫ ---
def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton(text="📅 Моя смена", callback_data="menu_owner_shift")],
        [InlineKeyboardButton(text="🤝 Смена партнёра", callback_data="menu_partner_shift")],
        [InlineKeyboardButton(text="📝 Добавить заметку", callback_data="menu_note")],
        [InlineKeyboardButton(text="❌ Удалить смену/заметку", callback_data="menu_delete")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# --- ХЕНДЛЕРЫ (логика из вашего скрипта) ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id not in [OWNER_ID, USER_TWO_ID]:
        await message.answer("Извините, у вас нет доступа к этому боту.")
        return
    
    role = "Владелец" if message.from_user.id == OWNER_ID else "Партнёр"
    await message.answer(
        f"Привет, {role}! Я бот для управления календарём.\n"
        f"Выбери действие:",
        reply_markup=get_main_keyboard()
    )

@dp.callback_query(F.data.startswith("menu_"))
async def handle_menu(callback: CallbackQuery, state: FSMContext):
    action = callback.data
    
    if action == "menu_owner_shift":
        if callback.from_user.id != OWNER_ID:
            await callback.answer("Только владелец может менять свою смену!", show_alert=True)
            return
        await state.update_data(shift_type="owner")
        await callback.message.answer("Введите дату (ГГГГ-ММ-ДД), чтобы отметить свою смену:")
        await state.set_state(Form.waiting_for_date)
        
    elif action == "menu_partner_shift":
        if callback.from_user.id != OWNER_ID:
            await callback.answer("Только владелец может менять смену партнёра!", show_alert=True)
            return
        await state.update_data(shift_type="partner")
        await callback.message.answer("Введите дату (ГГГГ-ММ-ДД), чтобы отметить смену партнёра:")
        await state.set_state(Form.waiting_for_date)
        
    elif action == "menu_note":
        await callback.message.answer("Введите дату (ГГГГ-ММ-ДД), на которую хотите добавить заметку:")
        await state.set_state(Form.waiting_for_note_text)
    
    elif action == "menu_delete":
        await callback.message.answer("Введите дату (ГГГГ-ММ-ДД), чтобы удалить все отметки на этот день:")
        await state.set_state(Form.waiting_for_date)
        await state.update_data(delete_mode=True)
    
    await callback.answer()

@dp.message(Form.waiting_for_date)
async def process_date(message: types.Message, state: FSMContext):
    date_str = message.text.strip()
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        await message.answer("Неверный формат даты. Попробуйте ГГГГ-ММ-ДД:")
        return

    data = await state.get_data()
    
    if data.get("delete_mode"):
        db = load_data()
        if date_str in db["shifts"]: del db["shifts"][date_str]
        if date_str in db["notes"]: del db["notes"][date_str]
        save_data(db)
        await message.answer(f"🗑 Все отметки на {date_str} удалены.")
        await state.clear()
        return

    shift_type = data.get("shift_type")
    if shift_type:
        db = load_data()
        if date_str not in db["shifts"]:
            db["shifts"][date_str] = {"owner": 0, "partner": 0}
        
        if shift_type == "owner":
            db["shifts"][date_str]["owner"] = 1
            await message.answer(f"✅ Ваша смена на {date_str} отмечена.")
        elif shift_type == "partner":
            db["shifts"][date_str]["partner"] = 1
            await message.answer(f"✅ Смена партнёра на {date_str} отмечена.")
        
        save_data(db)
        await state.clear()
    else:
        await state.update_data(note_date=date_str)
        await message.answer("Теперь введите текст заметки:")
        await state.set_state(Form.waiting_for_note_text)

@dp.message(Form.waiting_for_note_text)
async def process_note(message: types.Message, state: FSMContext):
    note_text = message.text.strip()
    data = await state.get_data()
    date_str = data.get("note_date")
    
    db = load_data()
    db["notes"][date_str] = note_text
    save_data(db)
    
    await message.answer(f"📝 Заметка на {date_str} сохранена:\n{note_text}")
    await state.clear()

# --- ЗАПУСК ---
async def start_bot():
    # Создаем файл data.json, если его нет
    if not os.path.exists(DATA_FILE):
        save_data({"shifts": {}, "notes": {}})
    await dp.start_polling(bot)

def run_flask():
    # Render передает порт через переменную окружения PORT
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=lambda: asyncio.run(start_bot()))
    bot_thread.start()
    
    # Запускаем Flask в основном потоке (чтобы Render видел открытый порт)
    run_flask()