# local ML setup with gpu (models serving and compute)

## Prerequisites
* NVIDIA Drivers & WSL2 installed on host.
* Docker Desktop configured with WSL2 backend.
* Hugging Face model access approved.

## Commands

```bash
# Initialize .env file
make setup

# Edit .env and add your HF_TOKEN
# HF_TOKEN=your_token_here

# Build and start server
make up
# Watch container logs
make logs
# Query the endpoint
make test
# Shutdown container
make down
# Clean container and delete cached weights
make clean
```
