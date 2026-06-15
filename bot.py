import os
import sys
import logging
import asyncio
import subprocess
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Config variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
VLLM_URL = "http://vllm-server:8000"

# Global state tracking
# States: "MENU", "STARTING", "CHATTING"
user_states = {}
# Holds the dynamic model name retrieved from vLLM
current_model_name = None
# Polling tasks tracker
polling_tasks = {}

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN environment variable not set. Exiting.")
    sys.exit(1)

async def check_vllm_running() -> bool:
    """Check if the vLLM server is up and responsive."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{VLLM_URL}/v1/models")
            if response.status_code == 200:
                global current_model_name
                data = response.json()
                if "data" in data and len(data["data"]) > 0:
                    current_model_name = data["data"][0]["id"]
                    logger.info(f"vLLM is running. Detected model: {current_model_name}")
                    return True
    except Exception:
        pass
    return False

async def start_vllm_container():
    """Execute docker compose to start the vllm-server service in manual profile."""
    logger.info("Triggering docker compose to start vllm-server...")
    # Run the compose command in /app where the docker-compose.yml resides
    proc = await asyncio.create_subprocess_exec(
        "docker", "compose", "--profile", "manual", "up", "-d", "vllm-server",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd="/app"
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f"Failed to start container: {stderr.decode()}")
        raise Exception(f"Docker Compose failed: {stderr.decode()}")
    logger.info("vllm-server container started successfully.")

async def stop_vllm_container():
    """Stop the vllm-server container."""
    logger.info("Triggering docker compose to stop vllm-server...")
    proc = await asyncio.create_subprocess_exec(
        "docker", "compose", "--profile", "manual", "stop", "vllm-server",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd="/app"
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f"Failed to stop container: {stderr.decode()}")
        raise Exception(f"Docker Compose stop failed: {stderr.decode()}")
    logger.info("vllm-server container stopped successfully.")

async def poll_vllm(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Background task to poll vLLM server until it is ready."""
    max_attempts = 120  # 10 minutes total (120 * 5s)
    attempt = 0

    while attempt < max_attempts:
        await asyncio.sleep(5)
        attempt += 1
        logger.info(f"Polling vLLM health, attempt {attempt}/{max_attempts}...")

        if await check_vllm_running():
            user_states[chat_id] = "CHATTING"
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🚀 vLLM server is fully loaded and ready!\nModel: `{current_model_name}`\n\nYou can now start typing your prompt here, and I will reply."
            )
            polling_tasks.pop(chat_id, None)
            return

    # Timeout reached
    user_states[chat_id] = "MENU"
    polling_tasks.pop(chat_id, None)
    await context.bot.send_message(
        chat_id=chat_id,
        text="⚠️ vLLM server startup timed out (exceeded 10 minutes). Please try again or check container logs."
    )

# --- Command Handlers ---

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /start command."""
    chat_id = update.effective_chat.id
    user_states[chat_id] = "MENU"

    # Cancel any active polling task for this user
    if chat_id in polling_tasks:
        polling_tasks[chat_id].cancel()
        polling_tasks.pop(chat_id, None)

    keyboard = [
        [
            InlineKeyboardButton("LLM 🤖", callback_data="select_llm"),
            InlineKeyboardButton("Diffusion Model 🎨", callback_data="select_diffusion"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Welcome! Please choose a model option below:", reply_markup=reply_markup
    )

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /stop command."""
    chat_id = update.effective_chat.id
    user_states[chat_id] = "MENU"

    # Cancel polling if running
    if chat_id in polling_tasks:
        polling_tasks[chat_id].cancel()
        polling_tasks.pop(chat_id, None)

    await update.message.reply_text("Stopping the vLLM server, please wait...")

    try:
        await stop_vllm_container()
        await update.message.reply_text("✅ vLLM server has been stopped. Use /start to boot it up again.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error stopping vLLM: {e}")

# --- Callback Handler ---

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for keyboard button selections."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    choice = query.data

    if choice == "select_llm":
        # Check if already running
        if await check_vllm_running():
            user_states[chat_id] = "CHATTING"
            await query.edit_message_text(
                f"🤖 vLLM is already running!\nModel: `{current_model_name}`\n\nYou can chat with it now."
            )
            return

        user_states[chat_id] = "STARTING"
        await query.edit_message_text(
            "⏳ Starting vLLM server...\nThis usually takes 2-4 minutes to download/load the weights. I'll notify you as soon as it's ready!"
        )

        try:
            await start_vllm_container()
            # Start background polling task
            task = asyncio.create_task(poll_vllm(chat_id, context))
            polling_tasks[chat_id] = task
        except Exception as e:
            user_states[chat_id] = "MENU"
            await query.message.reply_text(f"❌ Failed to start vLLM: {e}")

    elif choice == "select_diffusion":
        await query.message.reply_text("🎨 Diffusion Model is not implemented yet. Please select the LLM!")

# --- Message Handler ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for incoming text messages."""
    chat_id = update.effective_chat.id
    state = user_states.get(chat_id, "MENU")

    if state == "CHATTING":
        message_text = update.message.text
        # Show typing action to the user
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        # Verify model name is known
        global current_model_name
        if not current_model_name:
            if not await check_vllm_running():
                user_states[chat_id] = "MENU"
                await update.message.reply_text("❌ vLLM server appears to have stopped. Please use /start to restart it.")
                return

        payload = {
            "model": current_model_name,
            "messages": [{"role": "user", "content": message_text}],
            "temperature": 0.7,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{VLLM_URL}/v1/chat/completions",
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )

                if response.status_code == 200:
                    resp_data = response.json()
                    reply_text = resp_data["choices"][0]["message"]["content"]

                    # Split response if it exceeds Telegram's 4096 char limit
                    limit = 4000
                    if len(reply_text) > limit:
                        for i in range(0, len(reply_text), limit):
                            await update.message.reply_text(reply_text[i:i+limit])
                    else:
                        await update.message.reply_text(reply_text)
                else:
                    await update.message.reply_text(f"❌ Error from vLLM API (Status {response.status_code}): {response.text}")
        except Exception as e:
            logger.error(f"API Request Exception: {e}")
            await update.message.reply_text(f"❌ Connection error to vLLM server: {e}")

    elif state == "STARTING":
        await update.message.reply_text("⏳ Please wait, the vLLM server is still loading. I will notify you once it's ready!")
    else:
        await update.message.reply_text("🤖 Please use /start to select a model option first.")

# --- Main App ---

def main():
    """Start the bot."""
    logger.info("Initializing Telegram Bot...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Starting Telegram Bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
