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
# Skipping doc/man/changelog generation reduces how often dpkg's
# directory-rename path gets hit, but it does NOT eliminate the underlying
# bug — it's a real kernel/overlayfs limitation (EXDEV on directory renames
# across overlay layers in some sandboxed builders, Koyeb's included), not
# something fixable from inside this Dockerfile. The doc-exclusion trick
# alone still leaves plenty of packages failing.
# `git` has been dropped from this install entirely: nothing in this
# codebase calls git and requirements.txt has no `git+https://` entries,
# so it isn't needed at runtime. Its dependency chain (perl, libcurl3-gnutls,
# libssh2-1, libldap, libsasl2-2, librtmp1, libnghttp2-14, libpsl5,
# libbrotli1, liberror-perl, git-man...) accounted for the large majority
# of packages that were hitting the rename bug above. Installing only
# libmediainfo0v5 and its small dependency chain (libzen0v5, libtinyxml2-9,
# libmms0) cuts the number of packages exposed to the bug way down.
RUN mkdir -p /etc/dpkg/dpkg.cfg.d \
    && printf 'path-exclude=/usr/share/doc/*\npath-exclude=/usr/share/man/*\npath-exclude=/usr/share/groff/*\npath-exclude=/usr/share/info/*\n' > /etc/dpkg/dpkg.cfg.d/01-nodoc \
    && apt-mark hold ca-certificates \
    && apt-get update \
    && apt-get install -y --no-install-recommends libmediainfo0v5 \
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
