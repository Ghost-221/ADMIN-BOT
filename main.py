import asyncio
import logging
import aiosqlite
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- কনফিগারেশন ---
API_TOKEN = "YOUR_BOT_TOKEN_HERE"  # আপনার বটের টোকেন দিন

# একাধিক অ্যাডমিনের ID এখানে কমা (,) দিয়ে লিখুন
ADMIN_IDS = [123456789, 987654321, 1122334455] 

# --- লগিং এবং সেটআপ ---
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
DB_NAME = "bot_users.db"

# --- ডাটাবেস সেটআপ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                full_name TEXT,
                username TEXT,
                joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def add_user(user: types.User):
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute(
                "INSERT OR IGNORE INTO users (id, full_name, username) VALUES (?, ?, ?)", 
                (user.id, user.full_name, user.username)
            )
            await db.commit()
        except Exception as e:
            logging.error(f"DB Error: {e}")

async def get_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            count = await cursor.fetchone()
            return count[0]

async def get_all_users():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id FROM users") as cursor:
            return await cursor.fetchall()

# --- FSM স্টেটস (অ্যাডমিন প্যানেলের জন্য) ---
class AdminState(StatesGroup):
    waiting_for_broadcast_content = State()
    waiting_for_confirm = State()

# --- ১. সাধারণ ইউজারদের জন্য (Start) ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    # ইউজার সেভ করা
    await add_user(message.from_user)
    
    # ওয়েলকাম মেসেজ
    welcome_msg = (
        f"আসসালামু আলাইকুম, {message.from_user.first_name}! ❤️\n\n"
        "আমাদের অফিসিয়াল বটে আপনাকে স্বাগতম।\n"
        "যেকোনো আপডেটের জন্য আমাদের সাথেই থাকুন।"
    )
    await message.answer(welcome_msg)

# --- ২. অ্যাডভান্সড অ্যাডমিন প্যানেল ---

# অ্যাডমিন মেইন মেনু ফাংশন
async def send_admin_panel(message: types.Message):
    total_users = await get_stats()
    
    text = (
        f"🛡️ **Admin Control Panel**\n\n"
        f"👥 Total Users: `{total_users}`\n"
        f"👤 Current Admin: `{message.from_user.first_name}`\n"
        f"🤖 Bot Status: Active"
    )
    
    kb = InlineKeyboardBuilder()
    kb.button(text="📢 Broadcast Message", callback_data="admin_broadcast")
    kb.button(text="📂 Export User IDs", callback_data="admin_export")
    kb.button(text="📊 Refresh Stats", callback_data="admin_refresh")
    kb.adjust(1) 
    
    await message.answer(text, reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    # চেক করা হচ্ছে ইউজার ID আমাদের অ্যাডমিন লিস্টে আছে কিনা
    if message.from_user.id in ADMIN_IDS:
        await send_admin_panel(message)

# --- ৩. বাটন হ্যান্ডলিং (Callbacks) ---

@dp.callback_query(F.data.startswith("admin_"))
async def admin_callbacks(call: types.CallbackQuery, state: FSMContext):
    # বাটন চাপলে চেক করবে সে অ্যাডমিন কিনা
    if call.from_user.id not in ADMIN_IDS:
        return

    action = call.data.split("_")[1]

    if action == "refresh":
        total_users = await get_stats()
        text = (
            f"🛡️ **Admin Control Panel**\n\n"
            f"👥 Total Users: `{total_users}`\n"
            f"👤 Current Admin: `{call.from_user.first_name}`\n"
            f"🤖 Bot Status: Active"
        )
        try:
            await call.message.edit_text(text, reply_markup=call.message.reply_markup, parse_mode="Markdown")
        except:
            await call.answer("Already Updated!")

    elif action == "export":
        await call.answer("Generating file...")
        users = await get_all_users()
        filename = "users_list.txt"
        with open(filename, "w") as f:
            for user in users:
                f.write(f"{user[0]}\n")
        
        await call.message.answer_document(FSInputFile(filename), caption="📂 All User IDs")
        os.remove(filename) 

    elif action == "broadcast":
        await call.message.answer("📢 অনুগ্রহ করে আপনার ব্রডকাস্ট মেসেজটি দিন (Text, Photo, Video supported):")
        await state.set_state(AdminState.waiting_for_broadcast_content)
        await call.answer()

# --- ৪. ব্রডকাস্ট সিস্টেম ---

@dp.message(AdminState.waiting_for_broadcast_content)
async def process_broadcast_content(message: types.Message, state: FSMContext):
    # মেসেজ দেওয়ার সময় আবার অ্যাডমিন চেক
    if message.from_user.id not in ADMIN_IDS:
        return

    # মেসেজ টেম্পোরারি সেভ করা
    await state.update_data(msg_id=message.message_id, chat_id=message.chat.id)

    # প্রিভিউ এবং কনফার্ম বাটন
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Send Broadcast", callback_data="confirm_send")
    kb.button(text="❌ Cancel", callback_data="cancel_send")

    # প্রিভিউ দেখানো (যিনি মেসেজ দিচ্ছেন তাকেই দেখাবে)
    await message.copy_to(chat_id=message.from_user.id) 
    await message.answer("👆 উপরে আপনার মেসেজের প্রিভিউ। আপনি কি এটা সবাইকে পাঠাতে চান?", reply_markup=kb.as_markup())
    await state.set_state(AdminState.waiting_for_confirm)

@dp.callback_query(AdminState.waiting_for_confirm)
async def confirm_broadcast_send(call: types.CallbackQuery, state: FSMContext):
    if call.data == "cancel_send":
        await call.message.edit_text("❌ ব্রডকাস্ট বাতিল করা হয়েছে।")
        await state.clear()
        return

    # ব্রডকাস্ট শুরু
    data = await state.get_data()
    msg_id = data['msg_id']
    from_chat = data['chat_id']
    
    users = await get_all_users()
    total = len(users)
    
    status_msg = await call.message.edit_text(f"⏳ ব্রডকাস্ট শুরু হচ্ছে... (Total: {total})")
    
    success = 0
    blocked = 0
    
    for user in users:
        try:
            # যিনি ব্রডকাস্ট শুরু করেছেন তার চ্যাট থেকে কপি হবে
            await bot.copy_message(chat_id=user[0], from_chat_id=from_chat, message_id=msg_id)
            success += 1
            await asyncio.sleep(0.05) 
        except Exception:
            blocked += 1
            
    # রিপোর্ট পাঠানো (যিনি সেন্ড করেছেন তাকে)
    await bot.send_message(
        call.from_user.id,
        f"🎉 **Broadcast Completed!**\n\n"
        f"✅ Sent: {success}\n"
        f"🚫 Failed/Blocked: {blocked}",
        parse_mode="Markdown"
    )
    await status_msg.delete() # লোডিং মেসেজ ডিলিট
    await state.clear()

# --- রানার ---
async def main():
    await init_db()
    print("Bot is running with Multi-Admin Support...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped")
