# syntax=docker/dockerfile:1.7
FROM golang:1.26.8-alpine AS build
RUN apk add --no-cache ca-certificates git
RUN --mount=type=cache,target=/go/pkg/mod \
    go install go.opentelemetry.io/collector/cmd/builder@v0.157.0
WORKDIR /build
COPY --from=trace2receiver /go.mod /go.sum /src/trace2receiver/
COPY --from=trace2receiver /*.go /src/trace2receiver/
COPY --from=trace2receiver /internal /src/trace2receiver/internal
COPY builder-config.yaml .
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 builder --config=builder-config.yaml

FROM alpine:3.24
RUN apk add --no-cache ca-certificates
COPY --from=build /build/output/trace2-otelcol /usr/local/bin/trace2-otelcol
USER 1000:1000
ENTRYPOINT ["/usr/local/bin/trace2-otelcol"]
