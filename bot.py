import os
import sys
import json
import logging
import asyncio
import subprocess
import random
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
COMFYUI_URL = "http://diffusion-server:8188"

# Global state tracking
# States: "MENU", "STARTING_LLM", "CHATTING", "STARTING_DIFFUSION", "WAITING_PHOTO", "WAITING_PROMPT", "GENERATING"
user_states = {}
user_photos = {}  # Tracks downloaded image path for the chat_id
current_model_name = None
polling_tasks = {}

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN environment variable not set. Exiting.")
    sys.exit(1)

# --- Container Management Functions ---

async def run_compose_cmd(*args) -> str:
    """Helper to run docker compose commands asynchronously."""
    proc = await asyncio.create_subprocess_exec(
        "docker", "compose", "--profile", "manual", *args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd="/app"
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise Exception(f"Docker Compose failed: {stderr.decode()}")
    return stdout.decode()

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
                    return True
    except Exception:
        pass
    return False

async def check_comfyui_running() -> bool:
    """Check if the ComfyUI server is up and responsive."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{COMFYUI_URL}/system_stats")
            if response.status_code == 200:
                return True
    except Exception:
        pass
    return False

async def start_llm_mode():
    """Ensure ComfyUI is stopped and vLLM starts up."""
    logger.info("Stopping ComfyUI container (if running) and starting vLLM container...")
    await run_compose_cmd("stop", "diffusion-server")
    await run_compose_cmd("up", "-d", "vllm-server")

async def start_diffusion_mode():
    """Ensure vLLM is stopped and ComfyUI starts up."""
    logger.info("Stopping vLLM container (if running) and starting ComfyUI container...")
    await run_compose_cmd("stop", "vllm-server")
    await run_compose_cmd("up", "-d", "diffusion-server")

async def stop_all_servers():
    """Stop both containers to free up GPU resources."""
    logger.info("Stopping all manual containers...")
    await run_compose_cmd("stop", "vllm-server", "diffusion-server")

# --- Polling Tasks ---

async def poll_vllm(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Poll vLLM server health."""
    max_attempts = 120  # 10 minutes
    for attempt in range(1, max_attempts + 1):
        await asyncio.sleep(5)
        logger.info(f"Polling vLLM health, attempt {attempt}/{max_attempts}...")
        if await check_vllm_running():
            user_states[chat_id] = "CHATTING"
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🚀 vLLM server is fully loaded and ready!\nModel: `{current_model_name}`\n\nYou can now start typing your prompt here, and I will reply."
            )
            polling_tasks.pop(chat_id, None)
            return

    user_states[chat_id] = "MENU"
    polling_tasks.pop(chat_id, None)
    await context.bot.send_message(
        chat_id=chat_id,
        text="⚠️ vLLM server startup timed out (exceeded 10 minutes). Please try again or check container logs."
    )

async def poll_comfyui(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Poll ComfyUI server health."""
    max_attempts = 60  # 5 minutes (ComfyUI usually starts in 10-30s if ComfyUI-Boot is ready)
    for attempt in range(1, max_attempts + 1):
        await asyncio.sleep(5)
        logger.info(f"Polling ComfyUI health, attempt {attempt}/{max_attempts}...")
        if await check_comfyui_running():
            user_states[chat_id] = "WAITING_PHOTO"
            await context.bot.send_message(
                chat_id=chat_id,
                text="🎨 ComfyUI is fully loaded and ready!\n\nTo edit an image with Flux 2, please **upload a photo**.\nYou can add your text prompt as the photo caption, or send the prompt as a message after uploading."
            )
            polling_tasks.pop(chat_id, None)
            return

    user_states[chat_id] = "MENU"
    polling_tasks.pop(chat_id, None)
    await context.bot.send_message(
        chat_id=chat_id,
        text="⚠️ ComfyUI server startup timed out. Please try again or check container logs."
    )

# --- ComfyUI Generation Logic ---

async def comfy_upload_image(file_path: str) -> str:
    """Upload target image to ComfyUI /upload/image endpoint."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        with open(file_path, "rb") as f:
            files = {"image": (os.path.basename(file_path), f, "image/png")}
            response = await client.post(f"{COMFYUI_URL}/upload/image", files=files)
            if response.status_code == 200:
                return response.json()["name"]
            else:
                raise Exception(f"ComfyUI upload failed: {response.text}")

async def comfy_run_workflow(chat_id: int, context: ContextTypes.DEFAULT_TYPE, prompt: str, image_name: str):
    """Submits the Flux 2 workflow, polls progress and replies with the edited image."""
    user_states[chat_id] = "GENERATING"
    status_msg = await context.bot.send_message(chat_id=chat_id, text="⚡ Submitting workflow to ComfyUI...")

    try:
        # 1. Load the workflow template
        with open("/app/Flux2_single_image_edit.json", "r") as f:
            workflow = json.load(f)

        # 2. Inject parameters
        workflow["76"]["inputs"]["image"] = image_name
        workflow["75:74"]["inputs"]["text"] = prompt
        workflow["75:73"]["inputs"]["noise_seed"] = random.randint(1000000000000, 99999999999999)

        # 3. Submit prompt
        payload = {"prompt": workflow}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{COMFYUI_URL}/prompt", json=payload)
            if resp.status_code != 200:
                raise Exception(f"Failed to submit prompt to ComfyUI: {resp.text}")
            
            prompt_id = resp.json()["prompt_id"]
            await status_msg.edit_text("⏳ Generating image... (This can take 30-90 seconds on Flux 2)")

            # 4. Poll /history
            max_polls = 100
            for _ in range(max_polls):
                await asyncio.sleep(3)
                hist_resp = await client.get(f"{COMFYUI_URL}/history/{prompt_id}")
                if hist_resp.status_code == 200:
                    hist_data = hist_resp.json()
                    if prompt_id in hist_data:
                        # Generation finished!
                        outputs = hist_data[prompt_id]["outputs"]
                        # Node "9" is SaveImage
                        image_info = outputs["9"]["images"][0]
                        filename = image_info["filename"]
                        subfolder = image_info.get("subfolder", "")
                        image_type = image_info.get("type", "output")
                        
                        await status_msg.edit_text("📥 Fetching result from ComfyUI...")

                        # 5. Fetch image
                        view_url = f"{COMFYUI_URL}/view"
                        params = {"filename": filename, "subfolder": subfolder, "type": image_type}
                        img_resp = await client.get(view_url, params=params)
                        if img_resp.status_code == 200:
                            # 6. Send photo
                            await status_msg.delete()
                            await context.bot.send_photo(
                                chat_id=chat_id,
                                photo=img_resp.content,
                                caption=f"Here is your edited image!\nPrompt: *{prompt}*",
                                parse_mode="Markdown"
                            )
                            user_states[chat_id] = "WAITING_PHOTO"
                            return
                        else:
                            raise Exception("Failed to fetch generated image from ComfyUI.")
            
            raise Exception("ComfyUI generation timed out.")

    except Exception as e:
        logger.error(f"Generation error: {e}")
        await status_msg.edit_text(f"❌ Generation failed: {e}")
        user_states[chat_id] = "WAITING_PHOTO"
    finally:
        # Cleanup temp upload file if exists
        temp_file = user_photos.pop(chat_id, None)
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)

# --- Command Handlers ---

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /start command."""
    chat_id = update.effective_chat.id
    user_states[chat_id] = "MENU"
    
    # Cancel any active polling task for this user
    if chat_id in polling_tasks:
        polling_tasks[chat_id].cancel()
        polling_tasks.pop(chat_id, None)

    # Clean up any temp photo tracking
    temp_file = user_photos.pop(chat_id, None)
    if temp_file and os.path.exists(temp_file):
        os.remove(temp_file)

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
    """Handler for the /stop command to save GPU resources."""
    chat_id = update.effective_chat.id
    user_states[chat_id] = "MENU"
    
    if chat_id in polling_tasks:
        polling_tasks[chat_id].cancel()
        polling_tasks.pop(chat_id, None)

    await update.message.reply_text("Stopping all model servers to save GPU resources, please wait...")
    
    try:
        await stop_all_servers()
        await update.message.reply_text("✅ All model servers have been stopped. Use /start to boot them up again.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error stopping servers: {e}")

# --- Callback Handler ---

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for keyboard button selections."""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    choice = query.data
    
    if choice == "select_llm":
        if await check_vllm_running():
            user_states[chat_id] = "CHATTING"
            await query.edit_message_text(
                f"🤖 vLLM is already running!\nModel: `{current_model_name}`\n\nYou can chat with it now."
            )
            return

        user_states[chat_id] = "STARTING_LLM"
        await query.edit_message_text(
            "⏳ Stopping ComfyUI and starting vLLM server...\nThis usually takes 2-4 minutes to load the weights. I'll notify you as soon as it's ready!"
        )
        
        try:
            await start_llm_mode()
            task = asyncio.create_task(poll_vllm(chat_id, context))
            polling_tasks[chat_id] = task
        except Exception as e:
            user_states[chat_id] = "MENU"
            await query.message.reply_text(f"❌ Failed to start vLLM: {e}")
            
    elif choice == "select_diffusion":
        if await check_comfyui_running():
            user_states[chat_id] = "WAITING_PHOTO"
            await query.edit_message_text(
                "🎨 ComfyUI is already running!\n\nPlease upload a photo to edit, and add your prompt as the caption."
            )
            return

        user_states[chat_id] = "STARTING_DIFFUSION"
        await query.edit_message_text(
            "⏳ Stopping vLLM and starting ComfyUI (Flux 2)...\nThis can take 30-60 seconds. I'll notify you as soon as it's ready!"
        )
        
        try:
            await start_diffusion_mode()
            task = asyncio.create_task(poll_comfyui(chat_id, context))
            polling_tasks[chat_id] = task
        except Exception as e:
            user_states[chat_id] = "MENU"
            await query.message.reply_text(f"❌ Failed to start ComfyUI: {e}")

# --- Media and Message Handlers ---

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for photo uploads in Diffusion Mode."""
    chat_id = update.effective_chat.id
    state = user_states.get(chat_id, "MENU")
    
    if state != "WAITING_PHOTO" and state != "WAITING_PROMPT":
        await update.message.reply_text("🤖 Please switch to Diffusion Mode first using /start.")
        return

    # Cancel previous photo if it exists
    prev_file = user_photos.pop(chat_id, None)
    if prev_file and os.path.exists(prev_file):
        os.remove(prev_file)

    await context.bot.send_chat_action(chat_id=chat_id, action="upload_photo")
    
    # Download the photo to a local temp file
    photo_file = await context.bot.get_file(update.message.photo[-1].file_id)
    temp_filename = f"temp_{chat_id}.png"
    await photo_file.download_to_drive(temp_filename)
    user_photos[chat_id] = temp_filename

    # If the user included a caption, treat it as the prompt
    if update.message.caption:
        prompt = update.message.caption
        logger.info(f"Photo uploaded with caption prompt: {prompt}")
        
        try:
            image_name = await comfy_upload_image(temp_filename)
            asyncio.create_task(comfy_run_workflow(chat_id, context, prompt, image_name))
        except Exception as e:
            await update.message.reply_text(f"❌ Upload error: {e}")
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
            user_states[chat_id] = "WAITING_PHOTO"
    else:
        # Prompt user for the text prompt
        user_states[chat_id] = "WAITING_PROMPT"
        await update.message.reply_text(
            "🖼️ Photo received! Now please send me the text prompt describing what changes or edits to make to this image."
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for text prompts / chat messages."""
    chat_id = update.effective_chat.id
    state = user_states.get(chat_id, "MENU")
    
    if state == "CHATTING":
        # Forwarding messages to vLLM
        message_text = update.message.text
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
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
            
    elif state == "WAITING_PROMPT":
        # We got the text prompt for the photo previously uploaded
        prompt = update.message.text
        temp_filename = user_photos.get(chat_id)
        
        if not temp_filename or not os.path.exists(temp_filename):
            user_states[chat_id] = "WAITING_PHOTO"
            await update.message.reply_text("⚠️ Image file lost. Please upload a photo again.")
            return

        try:
            image_name = await comfy_upload_image(temp_filename)
            asyncio.create_task(comfy_run_workflow(chat_id, context, prompt, image_name))
        except Exception as e:
            await update.message.reply_text(f"❌ Upload error: {e}")
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
            user_states[chat_id] = "WAITING_PHOTO"
            
    elif state == "STARTING_LLM" or state == "STARTING_DIFFUSION":
        await update.message.reply_text("⏳ Please wait, the servers are still loading. I will notify you once ready!")
    elif state == "GENERATING":
        await update.message.reply_text("⏳ ComfyUI is currently processing your image. Please wait for completion.")
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
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Starting Telegram Bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
