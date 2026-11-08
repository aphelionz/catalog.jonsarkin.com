# MariaDB 11.8 from Debian 13 packages.
#
# Debian security fixes reach this image only through a rebuild. On the prod VM
# a weekly cron job runs `docker compose build --pull --no-cache db` and
# recreates the container (docs/prod-rebuild-runbook.md, step 9).
#
# docker-entrypoint.sh and healthcheck.sh are MariaDB's official image scripts,
# copied unchanged from
# https://github.com/MariaDB/mariadb-docker/tree/bdfe641466a5312bb97d06a1a4e1fb411c49e6b6/11.8
#   docker-entrypoint.sh  sha256 d98f4b3febace529b7042bf2880777bfcebdd691d1a122c50dd59ee40963a7d2
#   healthcheck.sh        sha256 592f89cb39308f8425693221b0476932918e2f3e451cad39b822e23ea0103ef0
# They give this image the official image's MARIADB_* init variables and
# healthcheck flags, which the compose files and runbooks use.
FROM debian:trixie-slim

# Same IDs as the official image, so volumes keep their owner.
RUN groupadd -r -g 999 mysql && useradd -r -u 999 -g mysql -d /var/lib/mysql mysql

# The server postinst initializes a datadir unless one looks configured. A fake
# one skips that; the entrypoint initializes the real volume on first start.
# The sed comments out Debian's bind-address (127.0.0.1) and its log and user
# lines, as the official image does.
RUN set -eux; \
    apt-get update; \
    mkdir -p /var/lib/mysql/mysql; \
    touch /var/lib/mysql/mysql/user.frm; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        mariadb-server mariadb-client gosu tzdata; \
    rm -rf /var/lib/apt/lists/* /var/lib/mysql; \
    mkdir -p /var/lib/mysql /run/mysqld /docker-entrypoint-initdb.d; \
    chown mysql:mysql /var/lib/mysql /run/mysqld; \
    chmod 1777 /run/mysqld; \
    find /etc/mysql/ -name '*.cnf' -print0 \
        | xargs -0 grep -lZE '^(bind-address|log|user\s)' \
        | xargs -r -0 sed -Ei 's/^(bind-address|log|user\s)/#&/'

ENV LANG=C.UTF-8
COPY catalog.cnf /etc/mysql/mariadb.conf.d/99-catalog.cnf
COPY docker-entrypoint.sh healthcheck.sh /usr/local/bin/

VOLUME /var/lib/mysql
ENTRYPOINT ["docker-entrypoint.sh"]
EXPOSE 3306
CMD ["mariadbd"]
