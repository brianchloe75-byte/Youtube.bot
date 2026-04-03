import os
import time
import json
import random
import asyncio
import yt_dlp
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile

# -------------------------------
# Configuration
# -------------------------------
TOKEN = os.getenv("YT_TOKEN")  # YouTube bot token
BOT_USERNAME = os.getenv("YT_BOT_USERNAME", "EarthsBestDownloader_bot")

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

# Folder for downloads
if not os.path.exists("downloads"):
    os.makedirs("downloads")

# Data storage
DATA_FILE = "yt_data.json"
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
        json.dump({"users": [], "downloads": 0, "referrals": {}}, f)

# Cache for file_id
cache = {}

# Semaphore for queueing downloads
semaphore = asyncio.Semaphore(3)

# Cooldown per user
cooldown = {}

# -------------------------------
# Web server to keep alive
# -------------------------------
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Alive")

def run_web():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

threading.Thread(target=run_web).start()

# -------------------------------
# Data helpers
# -------------------------------
def load_data():
    with open(DATA_FILE) as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

# -------------------------------
# Start command
# -------------------------------
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    user_id = str(message.from_user.id)
    args = message.get_args()

    data = load_data()

    if user_id not in data["users"]:
        data["users"].append(user_id)
        if args:
            ref = args
            data["referrals"][ref] = data["referrals"].get(ref, 0) + 1
    save_data(data)

    await message.reply(
        "🔥 YouTube Downloader\n\n"
        "⚡ Fast | Proxy + Retry Enabled\n"
        "🎯 Send YouTube link → choose quality\n\n"
        "🚀 Invite friends to grow!",
        parse_mode="Markdown"
    )

# -------------------------------
# Stats command
# -------------------------------
@dp.message_handler(commands=["stats"])
async def stats(message: types.Message):
    data = load_data()
    user_id = str(message.from_user.id)
    referrals = data["referrals"].get(user_id, 0)

    await message.reply(
        f"📊 Users: {len(data['users'])}\n"
        f"⬇️ Total Downloads: {data['downloads']}\n"
        f"👥 Your Referrals: {referrals}"
    )

# -------------------------------
# Proxy list
# -------------------------------
PROXIES = [
    None,  # fallback: no proxy
    # "http://user:pass@ip:port",  # optional: add working proxies
]

def get_random_proxy():
    return random.choice(PROXIES)

# -------------------------------
# yt-dlp options
# -------------------------------
def get_opts(filename, fmt):
    return {
        "format": fmt,
        "outtmpl": filename,
        "quiet": True,
        "nocheckcertificate": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 15,
        "proxy": get_random_proxy(),
        "http_headers": {"User-Agent": "Mozilla/5.0"}
    }

# -------------------------------
# Download helper
# -------------------------------
def download_video(url, user_id, choice):
    """
    Attempts YouTube download with smart retry + proxy
    """
    filename = f"downloads/{user_id}_{int(time.time())}.%(ext)s"

    formats = {
        "hd": ["bestvideo+bestaudio", "best"],
        "sd": ["best[height<=480]", "best"],
        "audio": ["bestaudio"]
    }

    last_error = None

    for attempt in range(3):  # retry loop
        for fmt in formats[choice]:
            try:
                ydl_opts = get_opts(filename, fmt)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    file_path = ydl.prepare_filename(info)
                    if os.path.exists(file_path):
                        return file_path, info
            except Exception as e:
                last_error = e
                continue
        time.sleep(2)  # small delay before retry

    raise Exception(f"Download failed: {last_error}")

# -------------------------------
# Handle messages
# -------------------------------
@dp.message_handler()
async def handle(message: types.Message):
    url = message.text.strip()
    user_id = message.from_user.id

    if not "youtube" in url.lower():
        return await message.reply("❌ Only YouTube links are supported here. Use the main bot for other platforms.")

    # Cooldown check
    now = time.time()
    if now - cooldown.get(user_id, 0) < 10:
        return await message.reply("⏳ Wait a few seconds before sending another link...")
    cooldown[user_id] = now

    # Cache
    if url in cache:
        return await message.reply_video(cache[url])

    msg = await message.reply("🔍 Fetching info...")

    try:
        ydl = yt_dlp.YoutubeDL({"quiet": True})
        info = ydl.extract_info(url, download=False)
        title = info.get("title", "YouTube Video")
        thumb = info.get("thumbnail")

        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("🎥 HD", callback_data=f"hd|{url}"))
        keyboard.add(InlineKeyboardButton("📱 SD", callback_data=f"sd|{url}"))
        keyboard.add(InlineKeyboardButton("🎧 Audio", callback_data=f"audio|{url}"))

        await msg.delete()
        await message.reply_photo(
            thumb,
            caption=f"🎬 {title}\nChoose quality:",
            reply_markup=keyboard
        )

    except Exception as e:
        print("Fetch error:", e)
        await msg.edit_text("❌ Failed to fetch video info.")

# -------------------------------
# Handle buttons
# -------------------------------
@dp.callback_query_handler(lambda c: True)
async def buttons(call: types.CallbackQuery):
    choice, url = call.data.split("|")
    user_id = call.from_user.id
    asyncio.create_task(process(call.message, url, user_id, choice))
    await call.answer()

# -------------------------------
# Process download/upload
# -------------------------------
async def process(message, url, user_id, choice):
    async with semaphore:
        msg = await message.reply("⏳ Queued for download...")

        try:
            await msg.edit_text("⬇️ Downloading...")
            loop = asyncio.get_event_loop()

            # Timeout to avoid hanging
            file_path, info = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: download_video(url, user_id, choice)),
                timeout=180
            )

            size = os.path.getsize(file_path) / (1024*1024)
            if size > 90:  # YouTube videos can be large
                os.remove(file_path)
                return await msg.edit_text("❌ File too large (>90MB)")

            await msg.edit_text("📤 Uploading...")

            with open(file_path, "rb") as f:
                sent = await message.reply_video(f)

            cache[url] = sent.video.file_id

            os.remove(file_path)

            # Update growth stats
            data = load_data()
            if str(user_id) not in data["users"]:
                data["users"].append(str(user_id))
            data["downloads"] += 1
            save_data(data)

            await msg.edit_text("✅ Done!\n📥 Save to gallery")
            await message.reply(f"🚀 Share this bot: @{EarthsBestDownloader_bot}")

        except asyncio.TimeoutError:
            await msg.edit_text("❌ Took too long. Try again.")
        except Exception as e:
            print("Processing error:", e)
            await msg.edit_text("❌ Failed to download. Try another link.")

# -------------------------------
# Start bot
# -------------------------------
print("🔥 YouTube Downloader Bot running...")
executor.start_polling(dp)