#!/bin/sh
set -eu

sudo ip link add dev wg0 type wireguard

# Runner config
echo "$INPUT_CLIENT_KEY" | sudo wg set wg0 private-key /dev/stdin
sudo wg set wg0 listen-port "$INPUT_CLIENT_LISTEN_PORT"
sudo ip address add "$INPUT_CLIENT_VPN_IP"/32 dev wg0

# Remote config
sudo wg set wg0 \
    peer "$INPUT_HOST_PUBLIC_KEY" \
    endpoint "$INPUT_HOST_ENDPOINT:$INPUT_HOST_PUBLIC_PORT" \
    allowed-ips "$INPUT_VPN_SUBNET" \
    persistent-keepalive 25

# VPN config
sudo ip link set dev wg0 up
sudo ip route add "$INPUT_VPN_SUBNET" dev wg0
