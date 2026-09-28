#!/bin/sh
# Runs as root with NET_ADMIN in the sandbox network namespace, then drops to the mitm user.
# The application container joins this namespace with every capability dropped, so it can
# neither change these rules nor bypass them.
set -eu

# Connections to any address need a route before the NAT rule can see them. Add a default
# route through a dummy interface (no network at all) or the internal network interface.
if ! ip route show default | grep -q default; then
    if ip link add lantern0 type dummy 2>/dev/null; then
        ip addr add 10.255.255.1/24 dev lantern0
        ip link set lantern0 up
        ip route add default dev lantern0
    else
        dev="$(ip -o link show | awk -F': ' '$2 != "lo" {print $2; exit}' | cut -d@ -f1)"
        if [ -n "$dev" ]; then ip route add default dev "$dev"; fi
    fi
fi
if ! ip route show default | grep -q default; then
    echo "lantern: cannot install a default route in this namespace" >&2
    exit 3
fi

# All TCP that is not loopback goes to mitmproxy. Everything else that leaves the namespace
# is dropped: no UDP (so no DNS except the local resolver, no QUIC), no ICMP, no IPv6.
# Docker's embedded resolver (127.0.0.11) is blocked too, so DNS cannot carry data out.
iptables -t nat -A OUTPUT -d 127.0.0.0/8 -j RETURN
iptables -t nat -A OUTPUT -p tcp -j REDIRECT --to-ports 8080
iptables -A OUTPUT -d 127.0.0.11/32 -j DROP
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -d 127.0.0.0/8 -j ACCEPT
iptables -P OUTPUT DROP
if command -v ip6tables >/dev/null 2>&1; then
    ip6tables -A OUTPUT -o lo -j ACCEPT 2>/dev/null || true
    ip6tables -P OUTPUT DROP 2>/dev/null || true
fi

# Every name resolves to a documentation address (RFC 5737); the query log shows which
# hosts the application looked up, including ones it reached over non-HTTP protocols.
chmod 0777 /capture
dnsmasq --no-resolv --no-hosts --address=/#/198.51.100.1 \
    --listen-address=127.0.0.1 --bind-interfaces --port=53 \
    --log-queries --log-facility=/capture/dns.log --user=mitm --pid-file=/run/dnsmasq.pid
echo "nameserver 127.0.0.1" > /etc/resolv.conf 2>/dev/null || true

exec setpriv --reuid=mitm --regid=mitm --clear-groups \
    mitmdump --mode transparent --listen-host 127.0.0.1 --listen-port 8080 \
    --set confdir=/lantern/ca --set connection_strategy=lazy --set upstream_cert=false \
    --set block_global=false --set termlog_verbosity=warn --set flow_detail=0 \
    -s /lantern/recorder.py
