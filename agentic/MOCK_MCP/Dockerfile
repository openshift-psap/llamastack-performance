FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

RUN mkdir -p /tmp/hf_cache && chmod 777 /tmp/hf_cache
ENV HF_HOME=/tmp/hf_cache
ENV NUM_TOOLS=1
ENV TOOL_RESPONSE_TOKENS=100
ENV TOOL_DESCRIPTION_TOKENS=0
ENV TOKENIZER_MODEL=Qwen/Qwen3-VL-30B-A3B-Instruct
ENV POOL_SIZE=50
ENV PORT=8000

EXPOSE 8000

CMD ["python", "server.py"]
