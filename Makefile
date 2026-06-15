.PHONY: setup build up down restart logs test clean

setup:
	@if [ ! -f .env ]; then echo "HF_TOKEN=" > .env; fi

build:
	docker compose build

up: setup
	docker compose up -d

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f

test:
	@curl -s -X POST http://localhost:8000/v1/chat/completions \
		-H "Content-Type: application/json" \
		-d '{"model": "google/gemma-4-12B-it-qat-w4a16-ct", "messages": [{"role": "user", "content": "hello"}], "temperature": 0.7}'

clean: down
	rm -rf hf_cache
