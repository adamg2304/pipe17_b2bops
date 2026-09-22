FROM python:3.12-slim

# wkhtmltopdf (headless via xvfb) for order slip / packing list PDFs
RUN apt-get update && apt-get install -y --no-install-recommends \
      wkhtmltopdf xvfb fontconfig fonts-dejavu-core \
 && printf '#!/bin/sh\nexec xvfb-run -a /usr/bin/wkhtmltopdf "$@"\n' > /usr/local/bin/wkhtmltopdf-headless \
 && chmod +x /usr/local/bin/wkhtmltopdf-headless \
 && rm -rf /var/lib/apt/lists/*
ENV WKHTMLTOPDF_BIN=/usr/local/bin/wkhtmltopdf-headless

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
