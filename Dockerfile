# Pin to a specific release for reproducible builds.
# Check https://hub.docker.com/r/vllm/vllm-openai/tags for available tags.
FROM vllm/vllm-openai:latest

RUN pip install --no-cache-dir hf_transfer
ENV HF_HUB_ENABLE_HF_TRANSFER=1
