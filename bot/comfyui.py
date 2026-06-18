import os
import html
import json
import random
import time
import uuid
import asyncio
import traceback
import websockets
from telegram.ext import ContextTypes

from .config import (
    logger, COMFYUI_URL, http_client,
    NODE_LOAD_IMAGE, NODE_POSITIVE_PROMPT, NODE_RANDOM_NOISE, NODE_SAVE_IMAGE,
    STATUS_EDIT_INTERVAL,
)
from .state import UserState, set_state, user_photos, get_workflow, get_workflow_path


def validate_prompt(prompt: str) -> str | None:
    """Validate prompt. Returns error message if invalid, or None if valid."""
    if not prompt or not prompt.strip():
        return "❌ Prompt rejected: The prompt cannot be empty or only spaces. Please send a valid descriptive prompt."
    if len(prompt.strip()) < 3:
        return "❌ Prompt rejected: The prompt is too short (must be at least 3 characters). Please send a descriptive prompt."
    return None


async def upload_image(file_path: str) -> str:
    """Upload target image to ComfyUI /upload/image endpoint."""
    with open(file_path, "rb") as f:
        files = {"image": (os.path.basename(file_path), f, "image/png")}
        response = await http_client.post(f"{COMFYUI_URL}/upload/image", files=files)
        if response.status_code == 200:
            return response.json()["name"]
        else:
            raise Exception(f"ComfyUI upload failed (HTTP {response.status_code}): {response.text}")


async def run_workflow(chat_id: int, context: ContextTypes.DEFAULT_TYPE, prompt: str, image_name: str, status_msg=None):
    """Submits the Flux 2 workflow, tracks progress via WebSocket and replies with the edited image."""
    set_state(chat_id, UserState.GENERATING)

    if status_msg is None:
        status_msg = await context.bot.send_message(chat_id=chat_id, text="⚡ Initializing generation...")
    else:
        await status_msg.edit_text("⚡ Initializing generation...")

    temp_file = user_photos.get(chat_id)

    try:
        # 1. Load the workflow template
        workflow_path = get_workflow_path(chat_id)
        wf_name = get_workflow(chat_id)
        await status_msg.edit_text(f"🔍 Loading workflow: <b>{html.escape(wf_name)}</b>...", parse_mode="HTML")
        try:
            with open(workflow_path, "r") as f:
                workflow = json.load(f)
        except Exception as file_err:
            raise Exception(f"Failed to read workflow file: {file_err}")

        # 2. Connect to ComfyUI WebSocket
        client_id = str(uuid.uuid4())
        ws_url = COMFYUI_URL.replace("http://", "ws://").replace("https://", "wss://") + f"/ws?clientId={client_id}"

        await status_msg.edit_text("🔌 Connecting to ComfyUI websocket...")

        async with websockets.connect(ws_url) as ws:
            # 3. Inject parameters
            workflow[NODE_LOAD_IMAGE]["inputs"]["image"] = image_name
            workflow[NODE_POSITIVE_PROMPT]["inputs"]["text"] = prompt
            workflow[NODE_RANDOM_NOISE]["inputs"]["noise_seed"] = random.randint(1000000000000, 99999999999999)

            # 4. Submit prompt
            await status_msg.edit_text("🔄 Submitting job to ComfyUI queue...")
            payload = {"prompt": workflow, "client_id": client_id}

            resp = await http_client.post(f"{COMFYUI_URL}/prompt", json=payload)
            if resp.status_code != 200:
                raise Exception(f"Failed to submit prompt: ComfyUI HTTP {resp.status_code} - {resp.text}")

            prompt_id = resp.json()["prompt_id"]
            await status_msg.edit_text(f"⏳ Render job registered (Job ID: `{prompt_id}`). Waiting for execution...")

            # 5. Listen to WebSocket messages with timeout and throttled edits
            last_edit_time = 0.0

            async def _listen():
                nonlocal last_edit_time
                async for msg_raw in ws:
                    if isinstance(msg_raw, bytes):
                        continue  # skip binary preview frames
                    msg = json.loads(msg_raw)
                    msg_type = msg.get("type")
                    data = msg.get("data", {})

                    if msg_type == "status":
                        queue_remaining = data.get("status", {}).get("exec_info", {}).get("queue_remaining", 0)
                        now = time.monotonic()
                        if queue_remaining > 0 and now - last_edit_time >= STATUS_EDIT_INTERVAL:
                            last_edit_time = now
                            await status_msg.edit_text(f"⏳ Job in queue. Remaining items: {queue_remaining}...")
                        continue

                    if data.get("prompt_id") != prompt_id:
                        continue

                    if msg_type == "execution_start":
                        await status_msg.edit_text("🚀 ComfyUI execution started!")
                        last_edit_time = time.monotonic()

                    elif msg_type == "executing":
                        node_id = data.get("node")
                        if node_id is None:
                            return  # Execution finished
                        now = time.monotonic()
                        if now - last_edit_time >= STATUS_EDIT_INTERVAL:
                            last_edit_time = now
                            node_info = workflow.get(str(node_id), {})
                            node_title = node_info.get("_meta", {}).get("title", node_info.get("class_type", str(node_id)))
                            await status_msg.edit_text(f"⚡ Executing: <b>{html.escape(node_title)}</b>...", parse_mode="HTML")

                    elif msg_type == "progress":
                        now = time.monotonic()
                        if now - last_edit_time < STATUS_EDIT_INTERVAL:
                            continue
                        last_edit_time = now
                        value = data.get("value", 0)
                        total = data.get("max", 1)
                        percent = int((value / total) * 100)
                        filled = int(10 * value // total)
                        bar = "🟦" * filled + "⬜" * (10 - filled)
                        await status_msg.edit_text(
                            f"🎨 Rendering image...\n"
                            f"Progress: {bar} <b>{percent}%</b>\n"
                            f"Step {value}/{total}",
                            parse_mode="HTML"
                        )

                    elif msg_type == "execution_error":
                        exc_type = data.get("exception_type", "UnknownError")
                        exc_msg = data.get("exception_message", "Unknown exception occurred.")
                        err_node_id = data.get("node_id")
                        node_type = data.get("node_type")
                        raise Exception(
                            f"ComfyUI Execution Error:\n"
                            f"• Type: {exc_type}\n"
                            f"• Message: {exc_msg}\n"
                            f"• Node: {node_type} (ID: {err_node_id})"
                        )

            try:
                await asyncio.wait_for(_listen(), timeout=300)  # 5 min max
            except asyncio.TimeoutError:
                raise Exception("ComfyUI generation timed out after 5 minutes.")

        # 6. Fetch final history
        await status_msg.edit_text("📥 Fetching generation result...")
        hist_resp = await http_client.get(f"{COMFYUI_URL}/history/{prompt_id}")
        if hist_resp.status_code != 200:
            raise Exception(f"Failed to fetch execution history (HTTP {hist_resp.status_code})")

        hist_data = hist_resp.json()
        if prompt_id not in hist_data:
            raise Exception(f"Prompt ID `{prompt_id}` not found in history after execution finished.")

        outputs = hist_data[prompt_id]["outputs"]
        if not outputs or NODE_SAVE_IMAGE not in outputs or "images" not in outputs[NODE_SAVE_IMAGE]:
            raise Exception("ComfyUI finished execution but returned no output image.")

        image_info = outputs[NODE_SAVE_IMAGE]["images"][0]
        filename = image_info["filename"]
        subfolder = image_info.get("subfolder", "")
        image_type = image_info.get("type", "output")

        await status_msg.edit_text("📥 Downloading final image from ComfyUI...")
        img_resp = await http_client.get(
            f"{COMFYUI_URL}/view",
            params={"filename": filename, "subfolder": subfolder, "type": image_type}
        )
        if img_resp.status_code != 200:
            raise Exception(f"Failed to download generated image (HTTP {img_resp.status_code})")

        await status_msg.delete()
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=img_resp.content,
            caption=f"Here is your edited image!\nPrompt: <b>{html.escape(prompt)}</b>",
            parse_mode="HTML"
        )
        set_state(chat_id, UserState.WAITING_PHOTO)
        return

    except asyncio.CancelledError:
        logger.info(f"Generation task cancelled for chat_id={chat_id}")
        set_state(chat_id, UserState.WAITING_PHOTO)

    except Exception as e:
        logger.error(f"Generation error: {e}")
        logger.error(f"Full generation traceback:\n{traceback.format_exc()}")

        if isinstance(e, KeyError):
            err_details = f"KeyError: {e} (Node not found in workflow file. This usually happens if the workflow is in standard Web UI format instead of 'API format', or if node IDs have changed.)"
        else:
            err_details = f"{type(e).__name__}: {e}"

        error_msg = f"❌ <b>Generation failed!</b>\n\n<b>Error:</b>\n<code>{html.escape(err_details)}</code>"
        try:
            await status_msg.edit_text(error_msg, parse_mode="HTML")
        except Exception:
            try:
                await context.bot.send_message(chat_id=chat_id, text=error_msg, parse_mode="HTML")
            except Exception:
                pass
        set_state(chat_id, UserState.WAITING_PHOTO)

    finally:
        user_photos.pop(chat_id, None)
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass
