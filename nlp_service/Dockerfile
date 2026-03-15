FROM python:3.11-slim

WORKDIR /app

# Model names (override at build time with --build-arg)
ARG NLP_EMBED_MODEL=intfloat/multilingual-e5-base
ARG NLP_EMOTION_MODEL_RU=cointegrated/rubert-tiny2-cedr-emotion-detection
ARG NLP_EMOTION_MODEL_EN=j-hartmann/emotion-english-distilroberta-base

# Persist as runtime env vars
ENV NLP_EMBED_MODEL=${NLP_EMBED_MODEL}
ENV NLP_EMOTION_MODEL_RU=${NLP_EMOTION_MODEL_RU}
ENV NLP_EMOTION_MODEL_EN=${NLP_EMOTION_MODEL_EN}

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download models at build time (cached in image)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${NLP_EMBED_MODEL}')"
RUN python -c "from transformers import pipeline; pipeline('text-classification', model='${NLP_EMOTION_MODEL_RU}', top_k=1)"
RUN python -c "from transformers import pipeline; pipeline('text-classification', model='${NLP_EMOTION_MODEL_EN}', top_k=1)"

COPY . .

EXPOSE 8100

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
