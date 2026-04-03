FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY shared/ ./shared/

COPY services/coder/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/coder/ ./services/coder/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8001

CMD ["uvicorn", "services.coder.main:app", "--host", "0.0.0.0", "--port", "8001"]
