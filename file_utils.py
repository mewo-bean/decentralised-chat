def receive_all(sock, length):
    """Получает точно заданное количество байт"""
    data = b''
    while len(data) < length:
        remaining = length - len(data)
        packet = sock.recv(4096 if remaining > 4096 else remaining)
        if not packet:
            return None
        data += packet
    return data

def send_all(sock, data):
    """Отправляет все данные"""
    total_sent = 0
    while total_sent < len(data):
        sent = sock.send(data[total_sent:])
        if sent == 0:
            raise RuntimeError("Соединение разорвано")
        total_sent += sent