import os
import time
import json
import random
import asyncio
import logging
import yt_dlp
from aiohttp import web

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

# -------------------------------
# CONFIG
# -------------------------------
TOKEN = os.getenv("YT_TOKEN")
BOT_USERNAME = os.getenv("YT_BOT_USERNAME", "EarthsBestDownloader_bot")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# -------------------------------
# STORAGE SETUP
# -------------------------------
if not os.path.exists("downloads"):
    os.makedirs("downloads")

DATA_FILE = "yt_data.json"
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
        json.dump({"users": [], "downloads": 0, "referrals": {}}, f)

cache = {}
semaphore = asyncio.Semaphore(2)
cooldown = {}

# -------------------------------
# WEB SERVER (ASYNC)
# -------------------------------
async def handle(request):
    return web.Response(text="Alive")

async def start_web():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# -------------------------------
# DATA HELPERS
# -------------------------------
def load_data():
    with open(DATA_FILE) as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

# -------------------------------
# PROXIES
# -------------------------------
PROXIES = [
    None,
    # "http://user:pass@ip:port"
]

def get_random_proxy():
    return random.choice(PROXIES)

# -------------------------------
# DOWNLOAD FUNCTION
# -------------------------------
def download_video(url, user_id, choice):
    filename = f"downloads/{user_id}_{int(time.time())}.%(ext)s"

    formats = {
        "hd": ["bestvideo+bestaudio", "best"],
        "sd": ["best[height<=480]", "best"],
        "audio": ["bestaudio"]
    }

    for attempt in range(3):
        for fmt in formats[choice]:
            try:
                opts = {
                    "format": fmt,
                    "outtmpl": filename,
                    "quiet": True,
                    "retries": 3,
                    "fragment_retries": 3,
                    "socket_timeout": 15,
                    "proxy": get_random_proxy(),
                }

                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    path = ydl.prepare_filename(info)

                    if os.path.exists(path):
                        return path

            except Exception as e:
                print("Retry error:", e)
                continue

        time.sleep(2)

    raise Exception("Download failed")

# -------------------------------
# START COMMAND
# -------------------------------
@dp.message()
async def handle_all(message: types.Message):
    text = message.text

    if text.startswith("/start"):
        user_id = str(message.from_user.id)
        args = text.replace("/start", "").strip()

        data = load_data()

        if user_id not in data["users"]:
            data["users"].append(user_id)
            if args:
                data["referrals"][args] = data["referrals"].get(args, 0) + 1

        save_data(data)

        return await message.answer(
            "🔥 YouTube Downloader\n\n"
            "⚡ Fast | Smart Retry Enabled\n"
            "🎯 Send a YouTube link\n\n"
            "🚀 Invite friends to grow!",
            parse_mode="Markdown"
        )

    if text == "/stats":
        data = load_data()
        user_id = str(message.from_user.id)

        return await message.answer(
            f"📊 Users: {len(data['users'])}\n"
            f"⬇️ Downloads: {data['downloads']}\n"
            f"👥 Referrals: {data['referrals'].get(user_id, 0)}"
        )

    # -------------------------------
    # HANDLE YOUTUBE LINK
    # -------------------------------
    url = text.strip()
    user_id = message.from_user.id

    if "youtube" not in url.lower():
        return await message.reply("❌ Only YouTube links allowed.")

    # cooldown
    if time.time() - cooldown.get(user_id, 0) < 10:
        return await message.reply("⏳ Slow down...")
    cooldown[user_id] = time.time()

    # cache
    if url in cache:
        return await message.reply_video(cache[url])

    msg = await message.reply("🔍 Fetching info...")

    try:
        ydl = yt_dlp.YoutubeDL({"quiet": True})
        info = ydl.extract_info(url, download=False)

        title = info.get("title", "YouTube Video")
        thumb = info.get("thumbnail")

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🎥 HD", callback_data=f"hd|{url}"),
                InlineKeyboardButton(text="📱 SD", callback_data=f"sd|{url}")
            ],
            [
                InlineKeyboardButton(text="🎧 Audio", callback_data=f"audio|{url}")
            ]
        ])

        await msg.delete()
        await message.reply_photo(
            thumb,
            caption=f"🎬 {title}\nChoose quality:",
            reply_markup=kb
        )

    except Exception as e:
        print("Fetch error:", e)
        await msg.edit_text("❌ Failed to fetch info.")

# -------------------------------
# BUTTON HANDLER
# -------------------------------
@dp.callback_query()
async def buttons(call: types.CallbackQuery):
    choice, url = call.data.split("|")
    asyncio.create_task(process(call.message, url, call.from_user.id, choice))
    await call.answer()

# -------------------------------
# PROCESS DOWNLOAD
# -------------------------------
async def process(message, url, user_id, choice):
    async with semaphore:
        msg = await message.answer("⏳ Processing...")

        loop = asyncio.get_event_loop()

        for attempt in range(3):
            try:
                await msg.edit_text(f"⬇️ Downloading ({attempt+1}/3)")

                file_path = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: download_video(url, user_id, choice)),
                    timeout=180
                )

                size = os.path.getsize(file_path) / (1024 * 1024)
                if size > 90:
                    os.remove(file_path)
                    return await msg.edit_text("❌ File too large")

                await msg.edit_text("📤 Uploading...")

                file = FSInputFile(file_path)
                sent = await message.answer_video(file)

                cache[url] = sent.video.file_id
                os.remove(file_path)

                data = load_data()
                data["downloads"] += 1
                save_data(data)

                return await msg.edit_text("✅ Done")

            except Exception as e:
                print("Retry fail:", e)
                await asyncio.sleep(1)

        await msg.edit_text("❌ Failed after retries")

# -------------------------------
# MAIN (AUTO-RESTART)
# -------------------------------
async def main():
    while True:
        try:
            print("🚀 YouTube Bot running...")

            await start_web()
            await dp.start_polling(bot)

        except Exception as e:
            print("💥 Crash:", e)
            await asyncio.sleep(5)

# -------------------------------
# START
# -------------------------------
if _name_ == "_main_":
    asyncio.run(main())
