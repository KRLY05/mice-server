FROM vllm/vllm-openai:nightly
RUN pip install --no-cache-dir hf_transfer
ENV HF_HUB_ENABLE_HF_TRANSFER=1
