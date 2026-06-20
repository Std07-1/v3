# Backend-образ. За замовч. ядро + NLLB. Для легкого образу прибери
# requirements-nllb.txt і вистав TRANSLATE_ENGINE=argos|mock.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Спершу залежності — кешується між збірками.
COPY requirements.txt requirements-nllb.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -r requirements-nllb.txt

COPY app ./app
COPY scripts ./scripts

# Статика фронту монтується ззовні або через nginx; шлях можна перевизначити.
ENV TRANSLATE_FRONTEND_DIR=/frontend

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
