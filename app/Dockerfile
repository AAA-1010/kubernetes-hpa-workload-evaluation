FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

RUN useradd --uid 10001 --no-create-home appuser
USER 10001

EXPOSE 8080
# Two worker processes with four threads each. The pod's CPU limit (set in
# k8s/deployment.yaml) caps how much CPU these workers can actually use.
CMD ["gunicorn", "--workers", "2", "--worker-class", "gthread", "--threads", "4", \
     "--bind", "0.0.0.0:8080", "--timeout", "30", "app:app"]
