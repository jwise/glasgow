FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends git make sdcc
RUN sdcc --version
COPY build.sh /build.sh
WORKDIR /work
CMD /build.sh
