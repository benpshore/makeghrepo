FROM rust:1-slim-bookworm AS build
WORKDIR /app
COPY . .
RUN cargo build --release

FROM debian:bookworm-slim
COPY --from=build /app/target/release/quiet-otter /usr/local/bin/app
USER nobody
CMD ["app"]
