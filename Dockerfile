# Base image swapped bullseye -> bookworm: the failure isn't coming from
# anything in this Dockerfile — it's Koyeb's builder choking while
# importing its OWN cached layer chain for python:3.10.8-slim-bullseye
# (a whiteout file baked into that base image's history at
# etc/ca-certificates, from when Debian's ca-certificates package was
# installed inside it). No command in this file touches that path.
# Switching the base tag points at a completely different image digest
# with no matching cache entry on Koyeb's side, so the poisoned cache
# import is skipped entirely instead of needing to be "fixed".
FROM python:3.10-slim-bookworm

# Install system dependencies
# NOTE: no "apt-get upgrade -y" here (removed previously) — but the real
# trigger turned out to be simpler than that: installing git/libmediainfo0v5
# makes dpkg re-run update-ca-certificates, which replaces the entire
# contents of /etc/ca-certificates. Docker's overlay filesystem records a
# full-directory replacement as an "opaque whiteout" marker, and writing
# that marker needs a filesystem permission (trusted.overlay.opaque xattr)
# that Koyeb's sandboxed builder doesn't allow — hence "operation not
# supported". `apt-mark hold ca-certificates` stops dpkg from touching it
# as a side effect of installing the other packages, so that marker never
# gets created in the first place.
# Root cause of the previous "Invalid cross-device link" dpkg error:
# dpkg unpacks .deb files into a scratch dir under $TMPDIR (default /tmp)
# then moves them into place — but in Koyeb's sandboxed builder, /tmp is a
# separate mount from /, so that move crosses filesystems and the kernel
# refuses it (EXDEV). Pointing TMPDIR at /var/tmp, which lives on the same
# filesystem as the rest of the image, keeps that move on one device.
RUN apt-mark hold ca-certificates \
    && export TMPDIR=/var/tmp \
    && apt-get update \
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
