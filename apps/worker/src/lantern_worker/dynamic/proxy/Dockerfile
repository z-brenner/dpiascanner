# Lantern sandbox proxy: DNS that answers every name with one address, a NAT rule that sends
# all TCP to mitmproxy, and a mitmproxy addon that records each request and answers it
# itself. Nothing is ever forwarded upstream.
FROM python:3.12-slim

# Optional extra CA for building behind a TLS-intercepting proxy. Empty unless the worker
# sets LANTERN_BUILD_CA_CERT; used for this build only.
COPY build-ca.crt /tmp/build-ca.crt
RUN if [ -s /tmp/build-ca.crt ]; then \
        cat /tmp/build-ca.crt >> /etc/ssl/certs/ca-certificates.crt; \
    fi \
 && apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends iptables dnsmasq iproute2 >/dev/null \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir --cert /etc/ssl/certs/ca-certificates.crt "mitmproxy==12.2.3" \
 && useradd --system --uid 10002 --no-create-home mitm \
 && mkdir -p /capture /lantern/ca \
 && chown mitm /lantern/ca

COPY entrypoint.sh recorder.py routes.py /lantern/
RUN chmod 0755 /lantern/entrypoint.sh
ENTRYPOINT ["/lantern/entrypoint.sh"]
