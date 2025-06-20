# file_utils.py
"""
Вспомогательные функции для работы с файлами и сокетами.
Содержит безопасные методы отправки, получения и сохранения файлов.
"""
import os
import socket
import time
import select

def receive_all(sock, length):
    """
    Получает точно заданное количество байт из сокета с таймаутом.

    :param sock: сокет
    :param length: количество байт
    :return: полученные данные (bytes) или None при ошибке/таймауте
    """
    data = b''
    start_time = time.time()
    while len(data) < length:
        if time.time() - start_time > 300.0:
            return None
            
        try:
            ready = select.select([sock], [], [], 1.0)
            if not ready[0]:
                continue
                
            remaining = length - len(data)
            packet = sock.recv(min(4096, remaining))
            if not packet:
                return None
            data += packet
        except (socket.timeout, BlockingIOError):
            continue
        except (ConnectionResetError, BrokenPipeError, OSError):
            return None
            
    return data

def send_all(sock, data):
    """
    Отправляет все байты в сокет, обрабатывая частичную отправку и ошибки.

    :param sock: сокет
    :param data: данные (bytes)
    :raises RuntimeError: если соединение разорвано
    """
    total_sent = 0
    while total_sent < len(data):
        try:
            sent = sock.send(data[total_sent:])
            if sent == 0:
                raise RuntimeError("Соединение разорвано")
            total_sent += sent
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            raise RuntimeError(f"Ошибка отправки: {e}")

def save_file(file_name, data):
    """
    Сохраняет данные в файл, избегая перезаписи существующих файлов.

    :param file_name: имя файла
    :param data: содержимое (bytes)
    :return: путь к сохранённому файлу
    """
    os.makedirs('downloads', exist_ok=True)
    path = os.path.join('downloads', file_name)

    counter = 1
    name, ext = os.path.splitext(file_name)
    while os.path.exists(path):
        path = os.path.join('downloads', f"{name}_{counter}{ext}")
        counter += 1

    with open(path, 'wb') as f:
        f.write(data)
    return path