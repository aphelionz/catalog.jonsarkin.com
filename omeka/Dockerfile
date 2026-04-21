FROM giocomai/omeka-s-docker:v4.2.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends libjpeg-turbo-progs \
    && rm -rf /var/lib/apt/lists/*
