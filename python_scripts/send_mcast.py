docker exec clab-poc-client8 python3 << 'EOF'
import socket
import time

MCAST_GRP = '239.0.0.1'
MCAST_PORT = 5000
SRC_IP = '10.255.80.2'
TTL = 64  # Set TTL high enough to traverse your network

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, TTL)
sock.bind((SRC_IP, 0))

print(f"Sending multicast from {SRC_IP} to {MCAST_GRP}:{MCAST_PORT} with TTL={TTL}")

for i in range(30):
    message = f"Packet {i:03d} from client8 at {time.strftime('%H:%M:%S')}".encode()
    sock.sendto(message, (MCAST_GRP, MCAST_PORT))
    print(f"Sent: {message.decode()}")
    time.sleep(1)

sock.close()
EOF