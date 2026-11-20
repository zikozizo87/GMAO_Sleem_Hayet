import socket
import uuid
import platform

def get_network_identity():
    hostname = socket.gethostname()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "127.0.0.1"

    mac_num = uuid.getnode()
    mac_address = ':'.join(['{:02x}'.format((mac_num >> elements) & 0xff) for elements in range(0, 2*6, 2)][::-1])

    print("=" * 40)
    print("      INFORMATIONS RÉSEAU & IDENTIFIANT PC      ")
    print("=" * 40)
    print(f" Nom du PC (Hostname) : {hostname}")
    print(f" Système d'exploitation : {platform.system()} {platform.release()}")
    print(f" Adresse IP locale    : {local_ip}")
    print(f" Adresse MAC (Wi-Fi/LAN): {mac_address.upper()}")
    print("=" * 40)

if __name__ == "__main__":
    get_network_identity()
