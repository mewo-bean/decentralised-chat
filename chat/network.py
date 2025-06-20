# chat_network.py
"""
Модуль chat_network реализует сетевую логику децентрализованного P2P-чата.
Обеспечивает обмен сообщениями, передачу файлов, поддержку пиров и пульсацию (heartbeat).
"""
import socket
import threading
import json
import os
import time
import uuid
import select
from enum import Enum
from chat.utils import receive_all, send_all

class MessageType(Enum):
    """Типы сообщений в сетевом протоколе."""
    TEXT = "TEXT"              # Текстовое сообщение
    FILE_META = "FMTA"         # Метаданные файла
    FILE_DATA = "FDAT"         # Данные файла
    NICK = "NICK"              # Смена ника
    PEER_LIST = "PERS"         # Список пиров
    HEARTBEAT = "BEAT"         # Проверка соединения
    CONNECTION_ID = "CONN"     # Установление соединения


class NetworkManager:
    """
    Основной класс для управления сетевыми соединениями и сообщениями.

    :param host: IP-адрес для прослушивания
    :param port: Порт
    :param gui_callback: функция для отправки событий в GUI или консоль
    :param debug: включает отладочный вывод
    """
    def __init__(self, host, port, gui_callback, debug=False):
        self.host = host
        self.port = port
        self.gui_callback = gui_callback
        self.nickname = f"User_{port}"
        self.debug = debug

        self.peers = {}
        self.connection_map = {}
        self.peer_nicks = {}
        self.known_peers = set()

        self.lock = threading.Lock()
        self.running = True
        self.heartbeat_interval = 5
        self.connection_id = str(uuid.uuid4())

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            self.sock.bind((self.host, self.port))
            self.sock.listen(5)
        except OSError as e:
            if "Address already in use" in str(e):
                raise OSError(f"Порт {self.port} уже занят!") from e
            raise

        threading.Thread(target=self.accept_connections, daemon=True).start()
        threading.Thread(target=self.heartbeat, daemon=True).start()
        
        if self.debug:
            print(f"[DEBUG] Сервер запущен на {self.host}:{self.port} (ID: {self.connection_id[:8]})")
        
        self.current_files = {} 

    def accept_connections(self):
        while self.running:
            try:
                conn, addr = self.sock.accept()
                threading.Thread(target=self.handle_incoming_connection, args=(conn, addr), daemon=True).start()
            except Exception as e:
                if self.running and not isinstance(e, OSError):
                    print(f"Ошибка при принятии соединения: {e}")

    def handle_incoming_connection(self, conn, addr):
        peer_id = None
        try:
            header = receive_all(conn, 8)
            if not header:
                return

            msg_type = MessageType(header[:4].decode())
            length = int.from_bytes(header[4:8], 'big')
            data = receive_all(conn, length)

            if not data or msg_type != MessageType.CONNECTION_ID:
                return

            try:
                conn_info = json.loads(data.decode())
                peer_id = conn_info['conn_id']
                peer_nick = conn_info.get('nickname', f"User_{addr[1]}")
            except json.JSONDecodeError:
                return

            if self.debug:
                print(f"Получен ID от {addr}: {peer_id[:8]}")

            with self.lock:
                if peer_id in self.peers:
                    if self.debug:
                        print(f"Уже подключены к пиру {peer_id[:8]}, закрываем дубликат")
                    conn.close()
                    return

                conn_data = json.dumps({
                    'conn_id': self.connection_id,
                    'nickname': self.nickname
                }).encode()
                self.send_message(MessageType.CONNECTION_ID, conn_data, conn)

                self.peers[peer_id] = (conn, addr)
                self.connection_map[addr] = peer_id
                self.peer_nicks[peer_id] = peer_nick

            self.send_peer_list(conn)
            self.gui_callback("update_peers", self.get_peer_list())
            
            self.handle_messages(conn, peer_id, addr)

        except Exception as e:
            print(f"Ошибка обработки входящего соединения: {e}")
        finally:
            if peer_id is None:
                try:
                    conn.close()
                except:
                    pass

    def handle_messages(self, conn, peer_id, addr):
        try:
            while self.running:
                ready = select.select([conn], [], [], 5.0)
                if not ready[0]:
                    continue
                    
                header = receive_all(conn, 8)
                msg_type = MessageType(header[:4].decode())
                length = int.from_bytes(header[4:8], 'big')
                if msg_type == MessageType.HEARTBEAT:
                    continue

                data = receive_all(conn, length)

                if not data:
                    if self.debug:
                        print(f"Пустые данные от {addr}")
                    break

                if msg_type == MessageType.FILE_META:
                    self.handle_file_meta(peer_id, data)
                elif msg_type == MessageType.FILE_DATA:
                    self.handle_file_data(peer_id, data)
                else:
                    if msg_type == MessageType.TEXT:
                        self.handle_text(peer_id, data)
                    elif msg_type == MessageType.NICK:
                        self.handle_nick_change(peer_id, data)
                    elif msg_type == MessageType.PEER_LIST:
                        self.handle_peer_list(data)
                    elif msg_type == MessageType.HEARTBEAT:
                        pass

        except (ConnectionResetError, BrokenPipeError):
            if self.debug:
                print(f"Соединение сброшено пиром: {addr}")
        except Exception as e:
            print(f"Ошибка обработки сообщений от {addr}: {e}")
        finally:
            if peer_id in self.peers:
                self.remove_peer(peer_id)

    def handle_file_meta(self, peer_id, data):
        """Обрабатывает метаданные файла"""
        try:
            file_info = json.loads(data.decode())
            file_name = file_info['name']
            file_size = file_info['size']
            
            os.makedirs('downloads', exist_ok=True)
            path = os.path.join('downloads', file_name)
            
            counter = 1
            base_name, ext = os.path.splitext(file_name)
            while os.path.exists(path):
                path = os.path.join('downloads', f"{base_name}_{counter}{ext}")
                counter += 1
                
            self.current_files[peer_id] = {
                'name': os.path.basename(path),
                'size': file_size,
                'received': 0,
                'file': open(path, 'wb')
            }
            self.gui_callback("message", 
                f"{self.peer_nicks.get(peer_id, 'Unknown')} отправляет файл: {file_name}")
        except Exception as e:
            print(f"Ошибка обработки метаданных файла: {e}")

    def handle_file_data(self, peer_id, data):
        """Обрабатывает данные файла"""
        if peer_id not in self.current_files:
            print(f"Получены данные файла без метаданных от {peer_id}")
            return
            
        file_info = self.current_files[peer_id]
        file_info['file'].write(data)
        file_info['received'] += len(data)
        
        if file_info['received'] >= file_info['size']:
            file_info['file'].close()
            self.gui_callback("message", 
                f"Файл {file_info['name']} получен!")
            del self.current_files[peer_id]

    def handle_text(self, peer_id, data):
        text = data.decode()
        self.gui_callback("message", f"{self.peer_nicks.get(peer_id, 'Unknown')}: {text}")

    def handle_nick_change(self, peer_id, data):
        nick = data.decode()
        with self.lock:
            self.peer_nicks[peer_id] = nick
        self.gui_callback("update_peers", self.get_peer_list())

    def handle_peer_list(self, data):
        try:
            peers = json.loads(data.decode())
            for peer in peers:
                peer_addr = (peer['host'], peer['port'])
                
                if peer_addr[0] == self.host and peer_addr[1] == self.port:
                    continue
                    
                if not self.is_connected_to(peer['host'], peer['port']):
                    threading.Thread(
                        target=self.connect_to_peer, 
                        args=(peer['host'], peer['port']), 
                        daemon=True
                    ).start()
        except Exception as e:
            print(f"Ошибка обработки списка пиров: {e}")

    def is_connected_to(self, host, port):
        with self.lock:
            for _, (_, addr) in self.peers.items():
                if addr[0] == host and addr[1] == port:
                    return True
            return False

    def send_peer_list(self, conn=None):
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
        header = msg_type.value.encode() + len(data).to_bytes(4, 'big')
        full_data = header + data
        try:
            if sock:
                send_all(sock, full_data)
            else:
                with self.lock:
                    peers_copy = list(self.peers.items())
                
                for peer_id, (peer_sock, _) in peers_copy:
                    try:
                        send_all(peer_sock, full_data)
                    except Exception as e:
                        if self.debug:
                            print(f"Ошибка отправки пиру {peer_id[:8]}: {e}")
                        if peer_id in self.peers:
                            self.remove_peer(peer_id)
        except Exception as e:
            print(f"Ошибка отправки: {e}")

    def connect_to_peer(self, host, port):
        if host in ['localhost', '127.0.0.1'] and port == self.port:
            return False

        if self.is_connected_to(host, port):
            return False

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(25)
            sock.connect((host, port))
            sock.settimeout(None)

            conn_info = json.dumps({
                'conn_id': self.connection_id,
                'nickname': self.nickname
            }).encode()
            self.send_message(MessageType.CONNECTION_ID, conn_info, sock)

            header = receive_all(sock, 8)
            if not header:
                return False

            msg_type = MessageType(header[:4].decode())
            length = int.from_bytes(header[4:8], 'big')
            data = receive_all(sock, length)

            if not data or msg_type != MessageType.CONNECTION_ID:
                return False

            try:
                conn_info = json.loads(data.decode())
                peer_id = conn_info['conn_id']
                peer_nick = conn_info.get('nickname', f"User_{port}")
            except json.JSONDecodeError:
                return False

            if self.debug:
                print(f"Успешное подключение к {host}:{port} (ID: {peer_id[:8]})")

            with self.lock:
                if peer_id in self.peers:
                    sock.close()
                    return False

                self.peers[peer_id] = (sock, (host, port))
                self.connection_map[(host, port)] = peer_id
                self.peer_nicks[peer_id] = peer_nick

            threading.Thread(
                target=self.handle_messages,
                args=(sock, peer_id, (host, port)),
                daemon=True
            ).start()

            self.send_peer_list(sock)
            self.gui_callback("update_peers", self.get_peer_list())
            return True
        except Exception as e:
            if self.debug:
                print(f"Ошибка подключения к {host}:{port}: {e}")
            return False

    def remove_peer(self, peer_id):
        """Удаляет пира при отключении"""
        if peer_id not in self.peers:
            return
        
        if self.debug:
            self.gui_callback("debug", f"Удаление пира {peer_id[:8]}")
        
        nick = self.peer_nicks.get(peer_id, 'Unknown')

        with self.lock:
            if peer_id not in self.peers:
                return
                
            sock, addr = self.peers[peer_id]
            try:
                sock.close()
            except:
                pass
                
            del self.peers[peer_id]
            if peer_id in self.peer_nicks:
                del self.peer_nicks[peer_id]
            if addr in self.connection_map:
                del self.connection_map[addr]
                
            if peer_id in self.current_files:
                file_info = self.current_files[peer_id]
                file_info['file'].close()
                os.remove(os.path.join('downloads', file_info['name']))
                del self.current_files[peer_id]
                
        self.send_peer_list()
        self.gui_callback("update_peers", self.get_peer_list())
        self.gui_callback("message", f"{nick} отключился")

    def get_peer_list(self):
        peers = []
        with self.lock:
            for peer_id, (_, addr) in self.peers.items():
                peers.append({
                    'address': f"{addr[0]}:{addr[1]}",
                    'nick': self.peer_nicks.get(peer_id, f"User_{addr[1]}")
                })
            peers.append({
                'address': f"{self.host}:{self.port}",
                'nick': self.nickname
            })
        return peers

    def send_text(self, text):
        self.send_message(MessageType.TEXT, text.encode())

    def send_file(self, file_path):
        """Отправляет файл с чанкованием"""
        try:
            file_name = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            
            file_meta = json.dumps({
                'name': file_name, 
                'size': file_size
            }).encode()
            self.send_message(MessageType.FILE_META, file_meta)
            
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    self.send_message(MessageType.FILE_DATA, chunk)
                    
            return True
        except Exception as e:
            print(f"Ошибка отправки файла: {e}")
            return False

    def change_nickname(self, new_nick):
        self.nickname = new_nick
        self.send_message(MessageType.NICK, new_nick.encode())
        self.gui_callback("update_peers", self.get_peer_list())

    def heartbeat(self):
        while self.running:
            time.sleep(self.heartbeat_interval)
            try:
                if self.debug:
                    print(f"[DEBUG] Отправка heartbeat")
                self.send_message(MessageType.HEARTBEAT, b'')
            except Exception as e:
                if self.debug:
                    print(f"[DEBUG] Ошибка heartbeat: {e}")

    def stop(self):
        self.running = False
        with self.lock:
            for peer_id in list(self.peers.keys()):
                self.remove_peer(peer_id)
        try:
            self.sock.close()
        except:
            pass