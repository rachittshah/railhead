FROM python:3.12-slim

RUN mkdir -p /srv/www && echo "booted" > /srv/www/index.html
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /app

# Harbor's docker env replaces CMD with `sh -c "sleep infinity"` but keeps ENTRYPOINT,
# so long-running services must be launched from the ENTRYPOINT, which then execs "$@".
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Never runs under Harbor (CMD is overridden). The verifier asserts port 9090 is dead.
CMD ["sh", "-c", "python -m http.server 9090 --directory /srv/www"]
