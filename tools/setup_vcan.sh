#!/usr/bin/env bash
# Create Linux virtual CAN interfaces for tests / offline use:  sudo bash tools/setup_vcan.sh
set -e
modprobe vcan
for i in 0 1; do
  ip link show "vcan$i" >/dev/null 2>&1 || ip link add dev "vcan$i" type vcan
  ip link set up "vcan$i"
done
ip -brief link show type vcan
