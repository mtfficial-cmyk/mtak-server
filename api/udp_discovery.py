import socket
import threading
import logging

class UDPDiscoveryService:
    """
    ATAK-CIV UDP Discovery Service.
    Listens for 'ATAK_DISCOVER_CHAT' on port 8091 and replies with the server URL.
    """
    def __init__(self, host="0.0.0.0", port=8091, server_url=""):
        self.host = host
        self.port = port
        self.server_url = server_url
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.running = False
        self.logger = logging.getLogger("UDPDiscovery")

    def get_local_ip(self):
        try:
            # Create a dummy socket to find the default route IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def start(self):
        self.running = True
        self.socket.bind((self.host, self.port))
        threading.Thread(target=self._run, daemon=True).start()
        self.logger.info(f"UDP Discovery listening on {self.host}:{self.port}")

    def _run(self):
        while self.running:
            try:
                data, addr = self.socket.recvfrom(1024)
                message = data.decode("utf-8").strip()
                if message == "ATAK_DISCOVER_CHAT":
                    # If server_url wasn't provided, build a local one
                    url = self.server_url if self.server_url else f"http://{self.get_local_ip()}:3000"
                    response = f"ATAK_CHAT_URL={url.replace('http://', 'ws://')}/chat"
                    self.socket.sendto(response.encode("utf-8"), addr)
                    self.logger.info(f"Responded to discovery from {addr} with {response}")
            except Exception as e:
                if self.running:
                    self.logger.error(f"UDP Discovery error: {e}")

    def stop(self):
        self.running = False
        self.socket.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    discovery = UDPDiscoveryService()
    discovery.start()
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        discovery.stop()
