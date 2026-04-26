FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY templates/ ./templates/

RUN mkdir -p /app/output /app/sessions

ENV OUTPUT_DIR=/app/output
ENV SESSIONS_DIR=/app/sessions

EXPOSE 7860

CMD ["python", "server.py"]
