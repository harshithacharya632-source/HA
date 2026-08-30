FROM python:3.10-slim-bookworm

# System dependencies for MediaInfo + Tesseract OCR
# (tesseract-ocr-eng needed for payment screenshot OCR in
# admin_plugins/payment_approval.py)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libmediainfo0v5 \
        tesseract-ocr \
        tesseract-ocr-eng \
    && ln -sf $(find /usr/lib -name "libmediainfo.so*" | head -1) /usr/local/lib/libmediainfo.so.0 \
    && ldconfig \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python deps
COPY requirements.txt /requirements.txt
RUN pip3 install --no-cache-dir --upgrade pip \
    && pip3 install --no-cache-dir -r /requirements.txt

# Set working directory
WORKDIR /VJ-FILTER-BOT

# Copy project files
COPY . .

# Run the bot
CMD ["python", "bot.py"]
