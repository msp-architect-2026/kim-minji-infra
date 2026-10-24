FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y \
    default-mysql-client \
    curl \
    && curl -O https://dl.min.io/client/mc/release/linux-arm64/mc \
    && chmod +x mc \
    && mv mc /usr/local/bin/mc \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

CMD ["/bin/sh"]