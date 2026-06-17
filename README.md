# vLLM Telegram Bot Controller

Telegram bot which spins up vllm

## Setup & Running

1. **Configure Environment**:
   ```bash
   make setup
   ```
   Edit `.env` and fill in:
   - `HF_TOKEN`
   - `TELEGRAM_BOT_TOKEN`
   - `COMFYUI_MODELS_PATH` (absolute path to ComfyUI models folder)
   - `DOCKER_SOCKET_PATH` (path to the docker socket, defaults to `/var/run/docker.sock`)
   - `HF_CACHE_PATH` (directory to store HuggingFace models cache, defaults to `./hf_cache`)

2. **Build and Run**:
   ```bash
   make build
   make up      # Starts the Telegram Bot (vLLM starts dynamically via bot menu)
   ```

## Commands Reference

- `make logs` - Watch Telegram bot logs
- `make logs-vllm` - Watch vLLM loading/completion logs
- `make down` - Stop bot and vLLM containers
- `make clean` - Stop containers and delete cached Hugging Face weights
- `make test` - Test the OpenAI endpoint locally

## Architecture Note
The bot container mounts `/var/run/docker.sock` to dynamically spin up the `vllm-server` service defined in `docker-compose.yml` (managed under the `manual` profile) when a user requests it. Send `/stop` in the Telegram chat to put vLLM container down.
