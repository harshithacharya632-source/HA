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
# Attempt 3 at the "Invalid cross-device link" dpkg error: both /tmp and
# /var/tmp turned out to hit it identically (see build history), which
# means it isn't really about temp-dir location — dpkg stages whole
# directories (usr/share/doc/X, etc/perl, etc.) and atomically renames them
# into place, and that's failing regardless of where the staging area is.
# CONFIRMED (see latest build log): this is not about docs, not about git,
# not about any specific package. The failure now hits real library paths
# too (./usr/lib/sasl2, ./usr/lib/x86_64-linux-gnu/sasl2) on packages that
# have nothing to do with docs or git. Every single package dpkg tries to
# --unpack on this builder fails the same way, because dpkg's install path
# always does an atomic backup-link + rename into place, and Koyeb's
# sandboxed builder's overlayfs can't do that rename (EXDEV) full stop.
# No dpkg.cfg.d exclusion or package-list trimming can route around this;
# it's a kernel/overlayfs limitation, not something this Dockerfile can
# negotiate with dpkg about.
# THE ACTUAL FIX: skip dpkg's install/unpack path entirely. `apt-get
# --download-only` just fetches the .deb files to /var/cache/apt/archives
# without touching them. `dpkg-deb -x` then extracts a .deb the same way
# `tar -x` would - plain file writes into the target tree, no atomic
# rename, no backup-link step - so it never executes the code path that's
# failing. This intentionally skips dpkg's package database (no `dpkg -l`
# entry, no apt upgrade tracking for these libs), which is a fine trade
# for a handful of runtime-only shared libraries in a single-purpose image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --download-only libmediainfo0v5 \
    && for deb in /var/cache/apt/archives/*.deb; do dpkg-deb -x "$deb" /; done \
    && ln -sf $(find /usr/lib -name "libmediainfo.so*" | head -1) /usr/local/lib/libmediainfo.so.0 \
    && ldconfig \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb

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
