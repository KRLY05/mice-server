# MICE Server — Telegram Bot for vLLM & ComfyUI

A Telegram bot that orchestrates **vLLM** (LLM chat) and **ComfyUI** (Flux 2 image editing) on a single GPU with automatic mutual exclusion — it stops one model server before starting the other to fit within VRAM constraints.

## Features

- **LLM Mode 🤖** — Chat with `google/gemma-4-12B-it-qat-w4a16-ct` via vLLM
- **Diffusion Mode 🎨** — Edit images with Flux 2 via ComfyUI
- **GPU Mutual Exclusion** — Only one model runs at a time to share a single GPU
- **Real-time Progress** — WebSocket-based progress bar for image generation
- **Error Reporting** — ComfyUI execution errors are surfaced directly in the chat

## Quick Start

### 1. Configure Environment

```bash
make setup
```

Edit `.env` and fill in:

| Variable | Description |
|---|---|
| `HF_TOKEN` | HuggingFace access token |
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/botfather) |
| `COMFYUI_MODELS_PATH` | Absolute host path to your ComfyUI models directory |
| `PROJECT_ROOT` | Absolute host path to this project (auto-set by `make setup`) |

### 2. Place Model Files

Ensure these files exist in your `COMFYUI_MODELS_PATH`:

```
models/
├── unet/FLUX2_FunStuff_distilledV12Fp8.safetensors
├── clip/qwen_3_8b_fp8mixed.safetensors
└── vae/full_encoder_small_decoder.safetensors
```

### 3. Build & Run

```bash
make build    # Build all Docker images
make up       # Start the Telegram bot
```

## Commands Reference

| Command | Description |
|---|---|
| `make setup` | Initialize `.env` template with auto-detected paths |
| `make build` | Build all Docker images (bot, vLLM, ComfyUI) |
| `make up` | Start the Telegram bot container |
| `make down` | Stop all containers |
| `make logs` | Stream Telegram bot logs |
| `make logs-vllm` | Stream vLLM server logs |
| `make logs-diff` | Stream ComfyUI server logs |
| `make test` | Test the vLLM OpenAI-compatible endpoint |
| `make clean` | Stop containers and delete HuggingFace cache |

## Bot Commands (Telegram)

| Command | Description |
|---|---|
| `/start` | Show the model selection menu |
| `/stop` | Stop all model servers (frees GPU VRAM) |
| `/help` | Show usage instructions |

## Architecture

The bot container uses Docker-out-of-Docker (DooD) to manage sibling containers:

```
┌─────────────┐     ┌─────────────────┐     ┌──────────────────┐
│ telegram-bot │────▶│   vllm-server   │     │ diffusion-server │
│  (always on) │     │  (GPU, manual)  │     │  (GPU, manual)   │
│              │────▶│                 │ OR  │                  │
└──────┬───────┘     └─────────────────┘     └──────────────────┘
       │
       ▼
  /var/run/docker.sock
```

The `manual` profile ensures GPU containers only start when explicitly requested by the user through the Telegram bot menu.
