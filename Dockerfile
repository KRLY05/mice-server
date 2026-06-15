FROM vllm/vllm-openai:v0.23.0
RUN pip install --no-cache-dir hf_transfer
ENV HF_HUB_ENABLE_HF_TRANSFER=1
