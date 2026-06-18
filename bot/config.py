import os
import sys
import logging
import httpx

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("mice-bot")

# Environment
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
VLLM_URL = os.environ.get("VLLM_URL", "http://vllm-server:8000")
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://diffusion-server:8188")
ALLOWED_GROUP_ID = os.environ.get("ALLOWED_GROUP_ID")

if ALLOWED_GROUP_ID:
    try:
        if not ALLOWED_GROUP_ID.startswith("@"):
            ALLOWED_GROUP_ID = int(ALLOWED_GROUP_ID)
    except ValueError:
        logger.error(f"Invalid ALLOWED_GROUP_ID: {ALLOWED_GROUP_ID}. Must be an integer or start with '@'.")
        ALLOWED_GROUP_ID = None
# Available ComfyUI workflows (stored on the host Windows machine under user/default/workflows/)
WORKFLOWS = {
    "Edit single image": "/comfyui-files/user/default/workflows/Flux2_single_image_edit.json"
}
DEFAULT_WORKFLOW = "Edit single image"

# Workflow node IDs (from Flux2_single_image_edit.json)
NODE_LOAD_IMAGE = "76"
NODE_POSITIVE_PROMPT = "75:74"
NODE_RANDOM_NOISE = "75:73"
NODE_SAVE_IMAGE = "9"

# Throttle interval for Telegram message edits (seconds)
STATUS_EDIT_INTERVAL = 2.0

# Shared HTTP client (closed on shutdown via post_shutdown hook)
http_client = httpx.AsyncClient(timeout=30.0)

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN environment variable not set. Exiting.")
    sys.exit(1)
