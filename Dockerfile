# Use newer Debian base (buster is EOL, use bullseye instead)
FROM python:3.10.8-slim-bullseye

# Install system dependencies
# NOTE: intentionally no "apt-get upgrade -y" — that's what was producing the
# ca-certificates opaque-whiteout file that Koyeb's build-cache importer
# can't handle ("failed to convert whiteout file ... operation not
# supported"), breaking the "COPY . ." step below via a corrupted cached
# layer. It also made every build pull a different, non-reproducible
# snapshot of every system package. `apt-get install` alone still pulls
# current versions of the packages actually being installed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git libmediainfo0v5 \
    && ln -sf $(find /usr/lib -name "libmediainfo.so*" | head -1) /usr/local/lib/libmediainfo.so.0 \
    && ldconfig \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python deps
COPY requirements.txt /requirements.txt
RUN pip3 install --no-cache-dir -U pip \
    && pip3 install --no-cache-dir -r /requirements.txt

# Set working directory
WORKDIR /VJ-FILTER-BOT

# Copy project files
COPY . .

# Run the bot
CMD ["python", "bot.py"]
