.PHONY: setup build up down restart logs logs-vllm logs-diff clean test

setup:
	@if [ ! -f .env ]; then \
		echo "HF_TOKEN=" > .env; \
		echo "TELEGRAM_BOT_TOKEN=" >> .env; \
		echo "COMFYUI_MODELS_PATH=" >> .env; \
		echo "DOCKER_SOCKET_PATH=/var/run/docker.sock" >> .env; \
		echo "HF_CACHE_PATH=./hf_cache" >> .env; \
		echo "PROJECT_ROOT=$$(pwd)" >> .env; \
		echo "✅ .env template initialized. Please add your tokens and paths."; \
	else \
		echo "ℹ️  .env already exists. Skipping."; \
	fi

build:
	docker compose --profile manual build

up: setup
	docker compose up -d

down:
	docker compose --profile manual down

restart:
	docker compose --profile manual restart

logs:
	docker compose logs -f telegram-bot

logs-vllm:
	docker compose --profile manual logs -f vllm-server

logs-diff:
	docker compose --profile manual logs -f diffusion-server

test:
	@curl -s -X POST http://localhost:8000/v1/chat/completions \
		-H "Content-Type: application/json" \
		-d '{"model": "google/gemma-4-12B-it-qat-w4a16-ct", "messages": [{"role": "user", "content": "hello"}], "temperature": 0.7}'

clean: down
	rm -rf hf_cache
