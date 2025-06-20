import socket
import threading
import json
import os
import time
import uuid
from enum import Enum
from file_utils import send_all, receive_all


class MessageType(Enum):
    TEXT = "TEXT"
    FILE = "FILE"
    NICK = "NICK"
    PEER_LIST = "PERS"
    HEARTBEAT = "BEAT"
    CONNECTION_ID = "CONN"


class NetworkManager:
    def __init__(self, host, port, gui_callback):
        self.host = host
        self.port = port
        self.gui_callback = gui_callback
        self.nickname = f"User_{port}"

        # Структуры для управления подключениями
        self.peers = {}  # {connection_id: (socket, address)}
        self.connection_map = {}  # {address: connection_id}
        self.peer_nicks = {}  # {connection_id: nickname}
        self.known_peers = set()  # Множество известных пиров (host:port)

        self.lock = threading.Lock()
        self.running = True
        self.heartbeat_interval = 5
        self.connection_id = str(uuid.uuid4())  # Уникальный ID для этого узла

        # Инициализация сокета
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(5)

        # Запуск потоков
        threading.Thread(target=self.accept_connections, daemon=True).start()
        threading.Thread(target=self.heartbeat, daemon=True).start()
        print(f"Сервер запущен на {self.host}:{self.port} (ID: {self.connection_id[:8]})")

    def accept_connections(self):
        """Принимает входящие соединения"""
        while self.running:
            try:
                conn, addr = self.sock.accept()
                threading.Thread(target=self.handle_incoming_connection, args=(conn, addr), daemon=True).start()
            except Exception as e:
                if self.running:
                    print(f"Ошибка при принятии соединения: {e}")
                break

    def handle_incoming_connection(self, conn, addr):
        """Обрабатывает входящее соединение"""
        try:
            # 1. Отправляем свой ID
            self.send_message(MessageType.CONNECTION_ID, self.connection_id.encode(), conn)

            # 2. Получаем ID от клиента
            header = receive_all(conn, 8)
            if not header:
                return

            msg_type = MessageType(header[:4].decode())
            length = int.from_bytes(header[4:8], 'big')
            data = receive_all(conn, length)

            if not data or msg_type != MessageType.CONNECTION_ID:
                return

            peer_id = data.decode()
            print(f"Получен ID от {addr}: {peer_id[:8]}")

            # 3. Проверяем, не подключены ли уже
            with self.lock:
                if peer_id in self.peers:
                    print(f"Уже подключены к пиру {peer_id[:8]}, закрываем дубликат")
                    conn.close()
                    return

                # Добавляем пира
                self.peers[peer_id] = (conn, addr)
                self.connection_map[addr] = peer_id
                self.known_peers.add(f"{addr[0]}:{addr[1]}")
                self.peer_nicks[peer_id] = f"User_{addr[1]}"

            # 4. Отправляем список пиров
            self.send_peer_list(conn)

            # 5. Запускаем обработчик сообщений
            self.handle_messages(conn, peer_id, addr)

        except Exception as e:
            print(f"Ошибка обработки входящего соединения: {e}")
        finally:
            if addr in self.connection_map:
                peer_id = self.connection_map[addr]
                self.remove_peer(peer_id)

    def handle_messages(self, conn, peer_id, addr):
        """Обрабатывает сообщения от пира"""
        try:
            while self.running:
                # Получение заголовка (тип + длина)
                header = receive_all(conn, 8)
                if not header:
                    print(f"Соединение с {addr} закрыто")
                    break

                msg_type = MessageType(header[:4].decode())
                length = int.from_bytes(header[4:8], 'big')
                data = receive_all(conn, length)

                if not data:
                    print(f"Пустые данные от {addr}")
                    break

                # Обработка сообщения по типу
                if msg_type == MessageType.TEXT:
                    self.handle_text(peer_id, data)
                elif msg_type == MessageType.FILE:
                    self.handle_file(peer_id, data)
                elif msg_type == MessageType.NICK:
                    self.handle_nick_change(peer_id, data)
                elif msg_type == MessageType.PEER_LIST:
                    self.handle_peer_list(data)
                elif msg_type == MessageType.HEARTBEAT:
                    pass

        except Exception as e:
            print(f"Ошибка обработки сообщений от {addr}: {e}")
        finally:
            if addr in self.connection_map:
                peer_id = self.connection_map[addr]
                self.remove_peer(peer_id)
            try:
                conn.close()
            except:
                pass

    def handle_text(self, peer_id, data):
        """Обрабатывает текстовое сообщение"""
        text = data.decode()
        self.gui_callback("message", f"{self.peer_nicks.get(peer_id, 'Unknown')}: {text}")

    def handle_file(self, peer_id, data):
        """Обрабатывает файл"""
        try:
            # Извлекаем метаданные и содержимое файла
            file_info = json.loads(data[:256].decode().strip('\x00'))
            file_name = file_info['name']
            file_size = file_info['size']
            file_data = data[256:256 + file_size]

            # Сохраняем файл
            save_file(file_name, file_data)
            self.gui_callback("message", f"{self.peer_nicks.get(peer_id, 'Unknown')} отправил файл: {file_name}")
        except Exception as e:
            print(f"Ошибка обработки файла: {e}")

    def handle_nick_change(self, peer_id, data):
        """Обновляет ник пира"""
        nick = data.decode()
        with self.lock:
            self.peer_nicks[peer_id] = nick
        self.gui_callback("update_peers", self.get_peer_list())

    def handle_peer_list(self, data):
        """Обновляет список пиров, избегая циклических подключений"""
        peers = json.loads(data.decode())
        for peer in peers:
            peer_addr = f"{peer['host']}:{peer['port']}"

            # Пропускаем себя и уже известные подключения
            if peer_addr == f"{self.host}:{self.port}":
                continue

            if peer_addr in self.known_peers:
                continue

            self.known_peers.add(peer_addr)

            # Подключаемся только к новым пирам
            if not self.is_connected_to(peer['host'], peer['port']):
                self.connect_to_peer(peer['host'], peer['port'])

    def is_connected_to(self, host, port):
        """Проверяет, подключены ли мы уже к этому пиру"""
        with self.lock:
            for peer_id, (_, addr) in self.peers.items():
                if addr[0] == host and addr[1] == port:
                    return True
            return False

    def send_peer_list(self, conn=None):
        """Отправляет текущий список пиров"""
        peers = []
        with self.lock:
            for peer_id, (_, addr) in self.peers.items():
                peers.append({
                    'host': addr[0],
                    'port': addr[1],
                    'nick': self.peer_nicks.get(peer_id, '')
                })

            peers.append({
                'host': self.host,
                'port': self.port,
                'nick': self.nickname
            })

        data = json.dumps(peers).encode()
        self.send_message(MessageType.PEER_LIST, data, conn)

    def send_message(self, msg_type, data, sock=None):
        """Отправляет сообщение"""
        header = msg_type.value.encode() + len(data).to_bytes(4, 'big')
        try:
            if sock:
                sock.send_all(header + data)
            else:
                with self.lock:
                    for peer_id, (peer_sock, _) in list(self.peers.items()):
                        try:
                            peer_sock.send_all(header + data)
                        except:
                            print(f"Ошибка отправки пиру {peer_id[:8]}")
                            self.remove_peer(peer_id)
        except Exception as e:
            print(f"Ошибка отправки: {e}")

    def connect_to_peer(self, host, port):
        """Подключается к другому пиру"""
        # Проверяем, не пытаемся ли подключиться к себе
        if host in ['localhost', '127.0.0.1'] and port == self.port:
            return False

        # Проверяем, не подключены ли уже
        if self.is_connected_to(host, port):
            return False

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            sock.connect((host, port))

            # 1. Отправляем свой ID
            self.send_message(MessageType.CONNECTION_ID, self.connection_id.encode(), sock)

            # 2. Получаем ID от сервера
            header = receive_all(sock, 8)
            if not header:
                return False

            msg_type = MessageType(header[:4].decode())
            length = int.from_bytes(header[4:8], 'big')
            data = receive_all(sock, length)

            if not data or msg_type != MessageType.CONNECTION_ID:
                return False

            peer_id = data.decode()
            print(f"Успешное подключение к {host}:{port} (ID: {peer_id[:8]})")

            with self.lock:
                # Проверяем, не добавили ли уже этого пира
                if peer_id in self.peers:
                    sock.close()
                    return False

                # Добавляем пира
                self.peers[peer_id] = (sock, (host, port))
                self.connection_map[(host, port)] = peer_id
                self.peer_nicks[peer_id] = f"User_{port}"
                self.known_peers.add(f"{host}:{port}")

            # 3. Запускаем обработчик сообщений
            threading.Thread(
                target=self.handle_messages,
                args=(sock, peer_id, (host, port)),
                daemon=True
            ).start()

            # 4. Отправляем список пиров
            self.send_peer_list(sock)

            return True
        except Exception as e:
            print(f"Ошибка подключения к {host}:{port}: {e}")
            return False

    def remove_peer(self, peer_id):
        """Удаляет пира при отключении"""
        print(f"Удаление пира {peer_id[:8]}")
        with self.lock:
            if peer_id in self.peers:
                sock, addr = self.peers[peer_id]

                # Закрываем сокет
                try:
                    sock.close()
                except:
                    pass

                # Удаляем из всех списков
                del self.peers[peer_id]
                if peer_id in self.peer_nicks:
                    del self.peer_nicks[peer_id]
                if addr in self.connection_map:
                    del self.connection_map[addr]

                # Обновляем список пиров у всех
                self.send_peer_list()

                self.gui_callback("update_peers", self.get_peer_list())
                self.gui_callback("message", f"{self.peer_nicks.get(peer_id, 'Unknown')} отключился")

    def get_peer_list(self):
        """Возвращает список пиров для GUI"""
        peers = []
        with self.lock:
            for peer_id, (_, addr) in self.peers.items():
                peers.append({
                    'address': f"{addr[0]}:{addr[1]}",
                    'nick': self.peer_nicks.get(peer_id, f"User_{addr[1]}")
                })
        return peers

    def send_text(self, text):
        """Отправляет текстовое сообщение"""
        self.send_message(MessageType.TEXT, text.encode())

    def send_file(self, file_path):
        """Отправляет файл"""
        try:
            with open(file_path, 'rb') as f:
                file_data = f.read()
            file_name = os.path.basename(file_path)
            file_info = json.dumps({'name': file_name, 'size': len(file_data)}).encode()
            file_info = file_info.ljust(256, b'\x00')  # Фиксированный размер заголовка
            self.send_message(MessageType.FILE, file_info + file_data)
            return True
        except Exception as e:
            print(f"Ошибка отправки файла: {e}")
            return False

    def change_nickname(self, new_nick):
        """Изменяет ник пользователя"""
        self.nickname = new_nick
        self.send_message(MessageType.NICK, new_nick.encode())
        self.gui_callback("update_peers", self.get_peer_list())

    def heartbeat(self):
        """Отправляет heartbeat для поддержания соединений"""
        while self.running:
            time.sleep(self.heartbeat_interval)
            try:
                self.send_message(MessageType.HEARTBEAT, b'')
            except:
                pass

    def stop(self):
        """Останавливает сетевые операции"""
        print("Остановка сетевого менеджера...")
        self.running = False
        with self.lock:
            for peer_id in list(self.peers.keys()):
                self.remove_peer(peer_id)
        try:
            self.sock.close()
        except:
            pass