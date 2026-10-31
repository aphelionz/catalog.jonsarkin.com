# Omeka S 4.2.1 on php:8.5.12-apache-trixie. Pinned by digest so a rebuild on
# prod gets the image that was tested locally.
FROM giocomai/omeka-s-docker:v4.2.1@sha256:9fe00058e917a34e515d64db972e84b4a1dde9d104b2d57fbb96c01268008a8e

RUN apt-get update \
    && apt-get install -y --no-install-recommends libjpeg-turbo-progs \
    && rm -rf /var/lib/apt/lists/*
