FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run Job entrypoint: runs to completion, then exits.
CMD ["python", "main.py"]
