import os
import html
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from .config import logger, VLLM_URL, http_client
from .state import UserState, get_state, set_state, user_photos, polling_tasks, generation_tasks
from .containers import (
    check_vllm_running, check_comfyui_running,
    start_llm_mode, start_diffusion_mode, stop_all_servers,
)
from .comfyui import validate_prompt, upload_image, run_workflow


# --- Polling Tasks ---

async def poll_vllm(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Poll vLLM server health."""
    max_attempts = 120  # 10 minutes
    for attempt in range(1, max_attempts + 1):
        await asyncio.sleep(5)
        logger.info(f"Polling vLLM health, attempt {attempt}/{max_attempts}...")
        is_running, model_name = await check_vllm_running()
        if is_running:
            set_state(chat_id, UserState.CHATTING)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🚀 vLLM server is fully loaded and ready!\nModel: <code>{html.escape(model_name)}</code>\n\nYou can now start typing your prompt here, and I will reply.",
                parse_mode="HTML"
            )
            polling_tasks.pop(chat_id, None)
            return

    set_state(chat_id, UserState.MENU)
    polling_tasks.pop(chat_id, None)
    await context.bot.send_message(
        chat_id=chat_id,
        text="⚠️ vLLM server startup timed out (exceeded 10 minutes). Please try again or check container logs."
    )


async def poll_comfyui(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Poll ComfyUI server health."""
    max_attempts = 60  # 5 minutes
    for attempt in range(1, max_attempts + 1):
        await asyncio.sleep(5)
        logger.info(f"Polling ComfyUI health, attempt {attempt}/{max_attempts}...")
        if await check_comfyui_running():
            set_state(chat_id, UserState.WAITING_PHOTO)
            await context.bot.send_message(
                chat_id=chat_id,
                text="🎨 ComfyUI is fully loaded and ready!\n\nTo edit an image with Flux 2, please **upload a photo**.\nYou can add your text prompt as the photo caption, or send the prompt as a message after uploading."
            )
            polling_tasks.pop(chat_id, None)
            return

    set_state(chat_id, UserState.MENU)
    polling_tasks.pop(chat_id, None)
    await context.bot.send_message(
        chat_id=chat_id,
        text="⚠️ ComfyUI server startup timed out. Please try again or check container logs."
    )


# --- Command Handlers ---

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /start command."""
    chat_id = update.effective_chat.id
    set_state(chat_id, UserState.MENU)

    # Cancel any active polling task
    task = polling_tasks.pop(chat_id, None)
    if task:
        task.cancel()

    # Cancel any active generation task
    gen_task = generation_tasks.pop(chat_id, None)
    if gen_task:
        gen_task.cancel()

    # Clean up any temp photo
    temp_file = user_photos.pop(chat_id, None)
    if temp_file and os.path.exists(temp_file):
        try:
            os.remove(temp_file)
        except Exception:
            pass

    keyboard = [[
        InlineKeyboardButton("LLM 🤖", callback_data="select_llm"),
        InlineKeyboardButton("Diffusion Model 🎨", callback_data="select_diffusion"),
    ]]
    await update.message.reply_text(
        "Welcome! Please choose a model option below:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /stop command to save GPU resources."""
    chat_id = update.effective_chat.id
    set_state(chat_id, UserState.MENU)

    task = polling_tasks.pop(chat_id, None)
    if task:
        task.cancel()

    gen_task = generation_tasks.pop(chat_id, None)
    if gen_task:
        gen_task.cancel()

    await update.message.reply_text("Stopping all model servers to save GPU resources, please wait...")
    try:
        await stop_all_servers()
        await update.message.reply_text("✅ All model servers have been stopped. Use /start to boot them up again.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error stopping servers: <code>{html.escape(str(e))}</code>", parse_mode="HTML")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /help command."""
    await update.message.reply_text(
        "📖 **Available Commands**\n\n"
        "/start — Show the model selection menu\n"
        "/stop — Stop all running model servers (frees GPU)\n"
        "/help — Show this help message\n\n"
        "**Diffusion Mode (Flux 2 Image Editing)**\n"
        "1. Select *Diffusion Model 🎨* from the menu\n"
        "2. Upload a photo (optionally include a prompt as the caption)\n"
        "3. Send a text prompt describing the edit you want\n"
        "4. Wait for the result!\n\n"
        "**LLM Mode**\n"
        "1. Select *LLM 🤖* from the menu\n"
        "2. Type your message and get a response",
        parse_mode="Markdown"
    )


# --- Callback Handler ---

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for keyboard button selections."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    choice = query.data

    if choice == "select_llm":
        is_running, model_name = await check_vllm_running()
        if is_running:
            set_state(chat_id, UserState.CHATTING)
            await query.edit_message_text(
                f"🤖 vLLM is already running!\nModel: <code>{html.escape(model_name)}</code>\n\nYou can chat with it now.",
                parse_mode="HTML"
            )
            return

        set_state(chat_id, UserState.STARTING_LLM)
        await query.edit_message_text(
            "⏳ Stopping ComfyUI and starting vLLM server...\n"
            "This usually takes 2-4 minutes to load the weights. I'll notify you as soon as it's ready!"
        )
        try:
            await start_llm_mode()
            task = asyncio.create_task(poll_vllm(chat_id, context))
            polling_tasks[chat_id] = task
        except Exception as e:
            set_state(chat_id, UserState.MENU)
            await query.message.reply_text(f"❌ Failed to start vLLM: <code>{html.escape(str(e))}</code>", parse_mode="HTML")

    elif choice == "select_diffusion":
        if await check_comfyui_running():
            set_state(chat_id, UserState.WAITING_PHOTO)
            await query.edit_message_text(
                "🎨 ComfyUI is already running!\n\nPlease upload a photo to edit, and add your prompt as the caption."
            )
            return

        set_state(chat_id, UserState.STARTING_DIFFUSION)
        await query.edit_message_text(
            "⏳ Stopping vLLM and starting ComfyUI (Flux 2)...\n"
            "This can take 30-60 seconds. I'll notify you as soon as it's ready!"
        )
        try:
            await start_diffusion_mode()
            task = asyncio.create_task(poll_comfyui(chat_id, context))
            polling_tasks[chat_id] = task
        except Exception as e:
            set_state(chat_id, UserState.MENU)
            await query.message.reply_text(f"❌ Failed to start ComfyUI: <code>{html.escape(str(e))}</code>", parse_mode="HTML")


# --- Media and Message Handlers ---

def _handle_task_done(chat_id: int):
    """Callback to clean up generation tasks and log exceptions."""
    def callback(task: asyncio.Task):
        generation_tasks.pop(chat_id, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error(f"Unhandled exception in background task: {exc}", exc_info=exc)
    return callback


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for photo uploads in Diffusion Mode."""
    chat_id = update.effective_chat.id
    state = get_state(chat_id)

    if state == UserState.GENERATING:
        await update.message.reply_text(
            "⏳ ComfyUI is currently processing your image. Please wait for completion before uploading another one."
        )
        return

    if state not in (UserState.WAITING_PHOTO, UserState.WAITING_PROMPT):
        if state == UserState.CHATTING:
            await update.message.reply_text(
                "🤖 I can only chat in LLM mode. If you want to edit images, please switch to Diffusion Model via /start first."
            )
        else:
            await update.message.reply_text("🤖 Please switch to Diffusion Mode first using /start.")
        return

    # Cancel previous photo if it exists
    has_prev = chat_id in user_photos
    prev_file = user_photos.pop(chat_id, None)
    if prev_file and os.path.exists(prev_file):
        try:
            os.remove(prev_file)
        except Exception:
            pass

    await context.bot.send_chat_action(chat_id=chat_id, action="upload_photo")

    # Download the photo to a local temp file
    photo_file = await context.bot.get_file(update.message.photo[-1].file_id)
    temp_filename = f"temp_{chat_id}.png"
    await photo_file.download_to_drive(temp_filename)
    user_photos[chat_id] = temp_filename

    if has_prev:
        await update.message.reply_text("✅ Image successfully received and accepted! (Previous image replaced)")
    else:
        await update.message.reply_text("✅ Image successfully received and accepted!")

    # If the user included a caption, treat it as the prompt
    if update.message.caption:
        prompt = update.message.caption
        logger.info(f"Photo uploaded with caption prompt: {prompt}")

        validation_error = validate_prompt(prompt)
        if validation_error:
            set_state(chat_id, UserState.WAITING_PROMPT)
            await update.message.reply_text(validation_error)
            return

        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f'📝 Prompt accepted: "{prompt}"\n\n📤 Uploading image to ComfyUI...'
        )
        try:
            image_name = await upload_image(temp_filename)
            task = asyncio.create_task(run_workflow(chat_id, context, prompt, image_name, status_msg))
            generation_tasks[chat_id] = task
            task.add_done_callback(_handle_task_done(chat_id))
        except Exception as e:
            logger.error(f"Upload error: {e}")
            await status_msg.edit_text(f"❌ Upload failed: <code>{html.escape(str(e))}</code>", parse_mode="HTML")
            user_photos.pop(chat_id, None)
            if os.path.exists(temp_filename):
                try:
                    os.remove(temp_filename)
                except Exception:
                    pass
            set_state(chat_id, UserState.WAITING_PHOTO)
    else:
        set_state(chat_id, UserState.WAITING_PROMPT)
        await update.message.reply_text(
            "✍️ Now, please send me a text prompt describing the edits or changes you want to apply to this image."
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for text prompts / chat messages."""
    chat_id = update.effective_chat.id
    state = get_state(chat_id)

    if state == UserState.CHATTING:
        message_text = update.message.text
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        is_running, model_name = await check_vllm_running()
        if not is_running:
            set_state(chat_id, UserState.MENU)
            await update.message.reply_text("❌ vLLM server appears to have stopped. Please use /start to restart it.")
            return

        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": message_text}],
            "temperature": 0.7,
        }

        try:
            response = await http_client.post(
                f"{VLLM_URL}/v1/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=60.0,
            )

            if response.status_code == 200:
                resp_data = response.json()
                reply_text = resp_data["choices"][0]["message"]["content"]
                limit = 4000
                if len(reply_text) > limit:
                    for i in range(0, len(reply_text), limit):
                        await update.message.reply_text(reply_text[i:i + limit])
                else:
                    await update.message.reply_text(reply_text)
            else:
                await update.message.reply_text(
                    f"❌ Error from vLLM API (Status {response.status_code}): <code>{html.escape(response.text)}</code>",
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"API Request Exception: {e}")
            await update.message.reply_text(f"❌ Connection error to vLLM server: <code>{html.escape(str(e))}</code>", parse_mode="HTML")

    elif state == UserState.WAITING_PHOTO:
        await update.message.reply_text(
            "📷 I'm waiting for a photo first. Please upload a photo to edit. "
            "You can write your prompt in the caption, or send it as a message after uploading."
        )

    elif state == UserState.WAITING_PROMPT:
        prompt = update.message.text

        validation_error = validate_prompt(prompt)
        if validation_error:
            await update.message.reply_text(validation_error)
            return

        temp_filename = user_photos.get(chat_id)
        if not temp_filename or not os.path.exists(temp_filename):
            set_state(chat_id, UserState.WAITING_PHOTO)
            await update.message.reply_text("⚠️ Image file lost. Please upload a photo again.")
            return

        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f'📝 Prompt accepted: "{prompt}"\n\n📤 Uploading image to ComfyUI...'
        )
        try:
            image_name = await upload_image(temp_filename)
            task = asyncio.create_task(run_workflow(chat_id, context, prompt, image_name, status_msg))
            generation_tasks[chat_id] = task
            task.add_done_callback(_handle_task_done(chat_id))
        except Exception as e:
            logger.error(f"Upload error: {e}")
            await status_msg.edit_text(f"❌ Upload failed: <code>{html.escape(str(e))}</code>", parse_mode="HTML")
            user_photos.pop(chat_id, None)
            if os.path.exists(temp_filename):
                try:
                    os.remove(temp_filename)
                except Exception:
                    pass
            set_state(chat_id, UserState.WAITING_PHOTO)

    elif state in (UserState.STARTING_LLM, UserState.STARTING_DIFFUSION):
        await update.message.reply_text("⏳ Please wait, the servers are still loading. I will notify you once ready!")
    elif state == UserState.GENERATING:
        await update.message.reply_text("⏳ ComfyUI is currently processing your image. Please wait for completion.")
    else:
        await update.message.reply_text("🤖 Please use /start to select a model option first.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error cleanly and report it in the chat if possible."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            error_msg = f"⚠️ <b>An unexpected system error occurred:</b>\n<code>{html.escape(str(context.error))}</code>"
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=error_msg,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to send error message to chat: {e}")
