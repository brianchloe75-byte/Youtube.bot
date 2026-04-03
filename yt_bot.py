import os
import asyncio
import time
import random
import yt_dlp
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile

# =========================
# Safety: load env variables
# =========================
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise ValueError("ERROR: TOKEN environment variable not set!")

# Optional: main bot username for redirect
MAIN_BOT = os.environ.get("MAIN_BOT_USERNAME", "@BrayC_bot")

# =========================
# Bot & Dispatcher
# =========================
bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

# =========================
# Download folder
# =========================
DOWNLOADS_DIR = "downloads"
if not os.path.exists(DOWNLOADS_DIR):
    os.makedirs(DOWNLOADS_DIR)

# =========================
# Queue system
# =========================
semaphore = asyncio.Semaphore(2)  # only 2 active downloads

# =========================
# Cache
# =========================
user_data = {}

# =========================
# Render keep-alive server
# =========================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Alive")

def run_web():
    port = int(os.environ.get("PORT", 10000))
    print(f"Web server listening on port {port}")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

threading.Thread(target=run_web).start()

# =========================
# Proxy rotation (optional)
# =========================
PROXIES = [None]  # add proxies here if needed

def get_random_proxy():
    return random.choice(PROXIES)

# =========================
# yt-dlp options
# =========================
def get_opts(filename, fmt):
    return {
        "format": fmt,
        "outtmpl": filename,
        "quiet": True,
        "nocheckcertificate": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 10,
        "http_headers": {"User-Agent": "Mozilla/5.0"},
        "proxy": get_random_proxy()
    }

# =========================
# Download function
# =========================
def download_video(url, user_id, choice):
    filename = f"{DOWNLOADS_DIR}/{user_id}_{int(time.time())}.%(ext)s"

    formats = {
        "hd": ["bestvideo+bestaudio", "best"],
        "sd": ["best[height<=480]", "best"],
        "audio": ["bestaudio"]
    }

    last_error = None
    for attempt in range(3):
        for fmt in formats[choice]:
            try:
                with yt_dlp.YoutubeDL(get_opts(filename, fmt)) as ydl:
                    info = ydl.extract_info(url, download=True)
                    file_path = ydl.prepare_filename(info)
                    if os.path.exists(file_path):
                        return file_path, info
            except Exception as e:
                last_error = e
                continue
        time.sleep(1)

    raise Exception(f"Download failed: {last_error}")

# =========================
# Start command
# =========================
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    await message.reply(
        f"🎬 YouTube Bot\n\n"
        f"⚡ Only handles YouTube links\n\n"
        f"🚀 Send a YouTube link to download"
    )

# =========================
# Handle messages
# =========================
@dp.message_handler()
async def handle_link(message: types.Message):
    url = message.text.strip()
    user_id = message.from_user.id

    if "youtube" not in url.lower():
        return await message.reply(
            f"⚠️ This bot only downloads YouTube videos.\n"
            f"Please use the main bot for other platforms: {MAIN_BOT}"
        )

    msg = await message.reply("🔍 Fetching info...")
    user_data[user_id] = {"url": url}

    # Send quality options
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🎥 HD", callback_data="hd"))
    keyboard.add(InlineKeyboardButton("📱 SD", callback_data="sd"))
    keyboard.add(InlineKeyboardButton("🎧 Audio", callback_data="audio"))

    await msg.delete()
    await message.reply(
        "Choose quality:",
        reply_markup=keyboard
    )

# =========================
# Button handler
# =========================
@dp.callback_query_handler(lambda c: True)
async def handle_buttons(call: types.CallbackQuery):
    user_id = call.from_user.id
    choice = call.data

    data = user_data.get(user_id)
    if not data:
        return await call.message.reply("❌ Send the link again")

    asyncio.create_task(process(call.message, data["url"], user_id, choice))
    await call.answer()

# =========================
# Process download queue
# =========================
async def process(message, url, user_id, choice):
    async with semaphore:
        msg = await message.reply("⏳ Queued...")

        try:
            await msg.edit_text("⬇️ Downloading...")
            loop = asyncio.get_event_loop()
            file_path, info = await loop.run_in_executor(
                None, lambda: download_video(url, user_id, choice)
            )

            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if size_mb > 49:
                os.remove(file_path)
                return await msg.edit_text("❌ File too large (>49MB)")

            await msg.edit_text("📤 Uploading...")

            if choice == "audio":
                await message.reply_audio(InputFile(file_path))
            else:
                await message.reply_video(InputFile(file_path))

            os.remove(file_path)
            await msg.edit_text("✅ Done!\n📥 Save to gallery")

        except Exception as e:
            print("PROCESS ERROR:", e)
            await msg.edit_text(f"❌ Failed: {e}")

# =========================
# Start polling
# =========================
print("🔥 YouTube bot running...")
executor.start_polling(dp)
