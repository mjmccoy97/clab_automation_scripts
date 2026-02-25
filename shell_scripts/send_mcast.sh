#!/bin/bash
# Multicast traffic generator for client8
# Sends continuous UDP multicast packets to 239.0.0.1:5000 with TTL=64

docker exec clab-poc-client8 python3 -c "
import socket
import time
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 64)
i = 0
while True:
    sock.sendto(f'Packet {i:03d} from client8 at {time.strftime(\"%H:%M:%S\")}'.encode(), ('239.0.0.1', 5000))
    print(f'Sent packet {i}')
    i += 1
    time.sleep(1)
"
