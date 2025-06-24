import json
import os
import select
import socket
import threading
import time
import uuid
from enum import Enum
from src.utils import receive_all, send_all

class MessageType(Enum):
    TEXT = 'TEXT'
    FILE_META = 'FMTA'
    FILE_DATA = 'FDAT'
    NICK = 'NICK'
    PEER_LIST = 'PERS'
    HEARTBEAT = 'BEAT'
    CONNECTION_ID = 'CONN'

class NetworkManager:
    def __init__(self, host, port, gui_callback, debug=False):
        '''Инициализирует сетевой менеджер.'''
        self.host = host
        self.port = port
        self.gui_callback = gui_callback
        self.nickname = f'User_{port}'
        self.debug = debug
        self.peers = {}  # peer_id -> (socket, (ip, port))
        self.connection_map = {}  # (ip, port) -> peer_id
        self.peer_nicks = {}  # peer_id -> nickname
        self.lock = threading.Lock()
        self.running = True
        self.heartbeat_interval = 5
        self.connection_id = str(uuid.uuid4())
        self.current_files = {}

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(5)
        self.server_ip = self.sock.getsockname()[0]

        if self.debug:
            print(f'[DEBUG] Сервер на {self.server_ip}:{self.port} (ID={self.connection_id[:8]})')

        threading.Thread(target=self.accept_connections, daemon=True).start()
        threading.Thread(target=self.heartbeat, daemon=True).start()

    def accept_connections(self):
        '''Принимает входящие соединения.'''
        while self.running:
            try:
                conn, addr = self.sock.accept()
                threading.Thread(
                    target=self.handle_incoming_connection,
                    args=(conn, addr),
                    daemon=True
                ).start()
            except OSError:
                break
            except Exception as e:
                print(f'Ошибка accept: {e}')

    def handle_incoming_connection(self, conn, addr):
        '''Обрабатывает входящее соединение.'''
        peer_id = None
        try:
            header = receive_all(conn, 8)
            if not header:
                return
            msg_type = MessageType(header[:4].decode())
            length = int.from_bytes(header[4:8], 'big')
            data = receive_all(conn, length)
            if msg_type != MessageType.CONNECTION_ID or not data:
                return

            conn_info = json.loads(data.decode())
            peer_id = conn_info['conn_id']
            peer_nick = conn_info.get('nickname', f'User_{addr[1]}')
            peer_port = conn_info.get('listen_port', addr[1])

            if peer_id == self.connection_id:
                if self.debug:
                    print(f"[DEBUG] Игнорируем собственный CONN (ID {peer_id[:8]})")
                return

            if self.debug:
                print(f"[DEBUG] CONN от {addr}: ID={peer_id[:8]}, nick={peer_nick}, listen_port={peer_port}")

            with self.lock:
                if peer_id in self.peers:
                    if self.debug:
                        print(f"[DEBUG] Дубликат {peer_id[:8]}")
                    conn.close()
                    return
                response = json.dumps({
                    'conn_id': self.connection_id,
                    'nickname': self.nickname,
                    'listen_port': self.port
                }).encode()
                self.send_message(MessageType.CONNECTION_ID, response, conn)
                self.peers[peer_id] = (conn, (addr[0], peer_port))
                self.connection_map[(addr[0], peer_port)] = peer_id
                self.peer_nicks[peer_id] = peer_nick

            self.gui_callback('message', f'Новый участник подключился: {peer_nick}')
            self.send_peer_list()
            self.gui_callback('update_peers', self.get_peer_list())
            self.handle_messages(conn, peer_id, (addr[0], peer_port))

        except Exception as e:
            print(f'Ошибка входящего соединения: {e}')
        finally:
            if peer_id is None:
                try:
                    conn.close()
                except:
                    pass

    def handle_messages(self, conn, peer_id, addr):
        '''Получает и обрабатывает сообщения от пира.'''
        try:
            while self.running:
                ready, _, _ = select.select([conn], [], [], 5.0)
                if not ready:
                    continue
                header = receive_all(conn, 8)
                if not header:
                    break
                msg_type = MessageType(header[:4].decode())
                length = int.from_bytes(header[4:8], 'big')
                data = receive_all(conn, length)
                if msg_type == MessageType.HEARTBEAT:
                    continue
                if not data:
                    break

                if msg_type == MessageType.TEXT:
                    text = data.decode()
                    self.gui_callback('message', f"{self.peer_nicks.get(peer_id, '?')}: {text}")
                elif msg_type == MessageType.NICK:
                    newnick = data.decode()
                    with self.lock:
                        self.peer_nicks[peer_id] = newnick
                    self.gui_callback('update_peers', self.get_peer_list())
                elif msg_type == MessageType.PEER_LIST:
                    self.handle_peer_list(data)
                elif msg_type == MessageType.FILE_META:
                    self.handle_file_meta(peer_id, data)
                elif msg_type == MessageType.FILE_DATA:
                    self.handle_file_data(peer_id, data)
        except Exception as e:
            if self.debug:
                print(f"[DEBUG] handle_messages error: {e}")
        finally:
            if peer_id in self.peers:
                self.remove_peer(peer_id)

    def handle_peer_list(self, data):
        '''Обрабатывает полученный список пиров.'''
        try:
            peers = json.loads(data.decode())
            if self.debug:
                print(f"[DEBUG] получен PERS: {peers}")
            for p in peers:
                host, port = p['host'], p['port']
                if host == self.server_ip and port == self.port:
                    continue
                if not self.is_connected_to(host, port):
                    threading.Thread(
                        target=self.connect_to_peer,
                        args=(host, port),
                        daemon=True
                    ).start()
        except Exception as e:
            print(f'Ошибка PERS: {e}')

    def send_peer_list(self, conn=None):
        '''Отправляет список пиров указанному соединению или всем.'''
        with self.lock:
            peers = [
                {'host': addr[0], 'port': addr[1], 'nick': self.peer_nicks.get(pid, '')}
                for pid, (_, addr) in self.peers.items()
            ]
            peers.append({'host': self.server_ip, 'port': self.port, 'nick': self.nickname})
        data = json.dumps(peers).encode()
        self.send_message(MessageType.PEER_LIST, data, conn)

    def get_peer_list(self):
        '''Возвращает список пиров для GUI.'''
        with self.lock:
            lst = [
                {'address': f"{addr[0]}:{addr[1]}", 'nick': self.peer_nicks.get(pid, f'User_{addr[1]}')}  
                for pid, (_, addr) in self.peers.items()
            ]
        lst.append({'address': f"{self.server_ip}:{self.port}", 'nick': self.nickname})
        return lst

    def connect_to_peer(self, host, port):
        '''Устанавливает исходящее соединение к пиру.'''
        if host == self.server_ip and port == self.port:
            return False
        if self.is_connected_to(host, port):
            return False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, port))
            info = json.dumps({
                'conn_id': self.connection_id,
                'nickname': self.nickname,
                'listen_port': self.port
            }).encode()
            self.send_message(MessageType.CONNECTION_ID, info, sock)

            header = receive_all(sock, 8)
            if not header:
                return False
            msg_type = MessageType(header[:4].decode())
            length = int.from_bytes(header[4:8], 'big')
            data = receive_all(sock, length)
            if msg_type != MessageType.CONNECTION_ID or not data:
                return False
            resp = json.loads(data.decode())
            peer_id = resp['conn_id']
            peer_nick = resp.get('nickname', '')
            peer_port = resp.get('listen_port', port)

            with self.lock:
                if peer_id in self.peers:
                    sock.close()
                    return False
                self.peers[peer_id] = (sock, (host, peer_port))
                self.connection_map[(host, peer_port)] = peer_id
                self.peer_nicks[peer_id] = peer_nick

            threading.Thread(
                target=self.handle_messages,
                args=(sock, peer_id, (host, peer_port)),
                daemon=True
            ).start()
            self.gui_callback('message', f'Новый участник подключился: {peer_nick}')
            self.send_peer_list(sock)
            self.gui_callback('update_peers', self.get_peer_list())
            return True
        except Exception as e:
            if self.debug:
                print(f"[DEBUG] connect_to_peer error {host}:{port}: {e}")
            return False

    def send_message(self, msg_type, data, sock=None):
        '''Упаковывает и отправляет сообщение.'''
        packet = msg_type.value.encode() + len(data).to_bytes(4, 'big') + data
        if sock:
            send_all(sock, packet)
        else:
            with self.lock:
                for pid, (s, _) in list(self.peers.items()):
                    try:
                        send_all(s, packet)
                    except Exception:
                        self.remove_peer(pid)

    def send_text(self, text):
        '''Отправляет текстовое сообщение всем пирами.'''
        self.send_message(MessageType.TEXT, text.encode())

    def handle_file_meta(self, peer_id, data):
        '''Обрабатывает метаданные входящего файла.'''
        try:
            info = json.loads(data.decode())
            name, size = info['name'], info['size']
            os.makedirs('downloads', exist_ok=True)
            path = os.path.join('downloads', name)
            base, ext = os.path.splitext(name)
            count = 1
            while os.path.exists(path):
                path = os.path.join('downloads', f"{base}_{count}{ext}")
                count += 1
            file_obj = open(path, 'wb')
            self.current_files[peer_id] = {'file': file_obj, 'size': size, 'received': 0, 'name': os.path.basename(path)}
            self.gui_callback('message', f"{self.peer_nicks.get(peer_id, '?')} отправляет файл: {name}")
        except Exception as e:
            print(f'Ошибка FILE_META: {e}')

    def handle_file_data(self, peer_id, data):
        '''Обрабатывает кусок данных файла.'''
        info = self.current_files.get(peer_id)
        if not info:
            return
        info['file'].write(data)
        info['received'] += len(data)
        if info['received'] >= info['size']:
            info['file'].close()
            self.gui_callback('message', f"Файл {info['name']} получен")
            del self.current_files[peer_id]

    def change_nickname(self, new_nick):
        '''Меняет ник и оповещает сеть.'''
        self.nickname = new_nick
        self.send_message(MessageType.NICK, new_nick.encode())
        self.gui_callback('update_peers', self.get_peer_list())

    def heartbeat(self):
        '''Отправляет HEARTBEAT через интервалы.'''
        while self.running:
            time.sleep(self.heartbeat_interval)
            try:
                if self.debug:
                    print('[DEBUG] heartbeat')
                self.send_message(MessageType.HEARTBEAT, b'')
            except:
                pass

    def remove_peer(self, peer_id):
        '''Удаляет пира и оповещает всех.'''
        with self.lock:
            if peer_id not in self.peers:
                return
            sock, addr = self.peers.pop(peer_id)
            nick = self.peer_nicks.pop(peer_id, 'Unknown')
            self.connection_map.pop(addr, None)
        try:
            sock.close()
        except:
            pass
        msg = f"{nick} отключился"
        self.send_message(MessageType.TEXT, msg.encode())
        self.gui_callback('message', msg)
        self.send_peer_list()
        self.gui_callback('update_peers', self.get_peer_list())

    def is_connected_to(self, host, port):
        '''Проверяет наличие подключения.'''
        with self.lock:
            return (host, port) in self.connection_map

    def stop(self):
        '''Останавливает менеджер и закрывает соединения.'''
        self.running = False
        try:
            self.sock.close()
        except:
            pass
        with self.lock:
            for pid in list(self.peers.keys()):
                self.remove_peer(pid)
