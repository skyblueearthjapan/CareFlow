# 箱（carelink-agent-sandbox）の出口プロキシ（carelink-agent-egress）。
# tinyproxy で CONNECT 先を許可リスト（allowlist）のホスト名だけに絞る。443 番だけ。
# 箱の網（internal）と外へ出る網の両方に入る、唯一のコンテナ。
ARG ALPINE_IMAGE=alpine:3.20
FROM ${ALPINE_IMAGE}
# hadolint ignore=DL3018
RUN apk add --no-cache tinyproxy tzdata \
 && addgroup -g 10003 egress \
 && adduser -D -H -u 10003 -G egress -s /sbin/nologin egress \
 && tinyproxy -v
COPY --chown=root:root tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY --chown=root:root allowlist /etc/tinyproxy/allowlist
RUN chmod 0444 /etc/tinyproxy/tinyproxy.conf /etc/tinyproxy/allowlist
USER 10003:10003
EXPOSE 3128
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
