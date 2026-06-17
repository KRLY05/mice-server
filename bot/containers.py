import asyncio
import subprocess

from .config import logger, VLLM_URL, COMFYUI_URL, http_client


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


async def check_vllm_running() -> tuple[bool, str | None]:
    """Check if the vLLM server is up. Returns (is_running, model_name)."""
    try:
        response = await http_client.get(f"{VLLM_URL}/v1/models", timeout=2.0)
        if response.status_code == 200:
            data = response.json()
            if "data" in data and len(data["data"]) > 0:
                return True, data["data"][0]["id"]
    except Exception:
        pass
    return False, None


async def check_comfyui_running() -> bool:
    """Check if the ComfyUI server is up and responsive."""
    try:
        response = await http_client.get(f"{COMFYUI_URL}/system_stats", timeout=2.0)
        return response.status_code == 200
    except Exception:
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
