# src/network.py
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
    FILE_ACCEPT = 'FACC'
    FILE_DECLINE = 'FDEC'
    NICK = 'NICK'
    PEER_LIST = 'PERS'
    HEARTBEAT = 'BEAT'
    CONNECTION_ID = 'CONN'
    CLEAR_HISTORY = 'CLRH'

class NetworkManager:
    def __init__(self, host, port, gui_callback, debug=False):
        '''Инициализирует сетевой менеджер с историей и приёмом файлов.'''
        self.host = host
        self.port = port
        self.gui_callback = gui_callback
        self.nickname = f'User_{port}'
        self.debug = debug
        self.peers = {}
        self.connection_map = {}
        self.peer_nicks = {}
        self.lock = threading.Lock()
        self.running = True
        self.heartbeat_interval = 5
        self.connection_id = str(uuid.uuid4())
        self.current_files = {}
        self.chat_history = []
        self.is_host = True
        self.pending_file = None
        self.pending_file_name = None

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(5)
        self.server_ip = self.sock.getsockname()[0]
        if self.debug:
            print(f"[DEBUG] Сервер на {self.server_ip}:{self.port} (ID={self.connection_id[:8]})")

        threading.Thread(target=self.accept_connections, daemon=True).start()
        threading.Thread(target=self.heartbeat, daemon=True).start()

    def accept_connections(self):
        '''Принимает входящие соединения и отправляет историю только если хост.'''
        while self.running:
            try:
                conn, addr = self.sock.accept()
                threading.Thread(
                    target=self._on_new_connection,
                    args=(conn, addr),
                    daemon=True
                ).start()
            except OSError:
                break
            except Exception as e:
                print(f'Ошибка accept: {e}')
    

    def _on_new_connection(self, conn, addr):
        peer_id, peer_addr = self._do_handshake(conn, addr)
        if not peer_id:
            conn.close()
            return

        if self.is_host:
            with self.lock:
                for msg in self.chat_history:
                    hdr = MessageType.TEXT.value.encode() + len(msg.encode()).to_bytes(4,'big')
                    try:
                        send_all(conn, hdr + msg.encode())
                    except Exception:
                        self.remove_peer(peer_id)
                        return

        self.handle_messages(conn, peer_id, peer_addr)



    def _do_handshake(self, conn, addr):
        header = receive_all(conn, 8)
        if not header:
            return None, None
        mt = MessageType(header[:4].decode())
        ln = int.from_bytes(header[4:8], 'big')
        data = receive_all(conn, ln)
        if mt != MessageType.CONNECTION_ID or not data:
            return None, None
        info = json.loads(data.decode())
        peer_id = info['conn_id']
        peer_nick = info.get('nickname', f'User_{addr[1]}')
        peer_port = info.get('listen_port', addr[1])
        if peer_id == self.connection_id or peer_id in self.peers:
            return None, None

        resp = json.dumps({
            'conn_id': self.connection_id,
            'nickname': self.nickname,
            'listen_port': self.port
        }).encode()
        self.send_message(MessageType.CONNECTION_ID, resp, conn)

        with self.lock:
            self.peers[peer_id] = (conn, (addr[0], peer_port))
            self.connection_map[(addr[0], peer_port)] = peer_id
            self.peer_nicks[peer_id] = peer_nick

        self.gui_callback('message', f'{peer_nick} подключился')
        self.chat_history.append(f'{peer_nick} подключился')
        self.send_peer_list()
        self.gui_callback('update_peers', self.get_peer_list())
        return peer_id, (addr[0], peer_port)



    def _handle_connect_send_history(self, conn, addr):
        '''Обрабатывает соединение и шлет историю.'''
        self.handle_incoming_connection(conn, addr)
        for msg in self.chat_history:
            header = MessageType.TEXT.value.encode() + len(msg.encode()).to_bytes(4, 'big')
            send_all(conn, header + msg.encode())

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

            info = json.loads(data.decode())
            peer_id = info['conn_id']
            peer_nick = info.get('nickname', f'User_{addr[1]}')
            peer_port = info.get('listen_port', addr[1])
            if peer_id == self.connection_id:
                return

            with self.lock:
                if peer_id in self.peers:
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

            self.gui_callback('message', f'{peer_nick} подключился')
            self.chat_history.append(f'{peer_nick} подключился')
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


    def handle_file_meta(self, peer_id, data):
        '''Обрабатывает метаданные входящего файла.'''
        info = json.loads(data.decode())
        name, size = info['name'], info['size']
        os.makedirs('downloads', exist_ok=True)
        path = os.path.join('downloads', name)
        base, ext = os.path.splitext(name)
        count = 1
        while os.path.exists(path):
            path = os.path.join('downloads', f"{base}_{count}{ext}")
            count += 1
        f = open(path, 'wb')
        self.current_files[peer_id] = {'file': f, 'size': size, 'received': 0, 'path': path}
        self.gui_callback('message', f"{self.peer_nicks.get(peer_id, '?')} отправляет файл: {name}")


    def handle_messages(self, conn, peer_id, addr):
        '''Обрабатывает входящие сообщения.'''
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
                data = b''
                if length > 0:
                    data = receive_all(conn, length)
                    if data is None:
                        break

                if msg_type == MessageType.HEARTBEAT:
                    continue

                if msg_type == MessageType.TEXT:
                    text = data.decode()
                    self.gui_callback('message', text)
                    self.chat_history.append(text)

                elif msg_type == MessageType.FILE_META:
                    raw_header = msg_type.value.encode() + length.to_bytes(4, 'big')
                    if self.is_host:
                        with self.lock:
                            for pid, (sock, _) in self.peers.items():
                                if pid == peer_id:
                                    continue
                                try:
                                    send_all(sock, raw_header + data)
                                except Exception:
                                    pass
                    self.handle_file_meta(peer_id, data)
                    meta = json.loads(data.decode())
                    self.gui_callback('file_request', (peer_id, meta['name'], meta['size']))

                elif msg_type == MessageType.FILE_ACCEPT:
                    accepter = self.peer_nicks.get(peer_id, '?')
                    notify = f'Пользователь {accepter} получил файл {self.pending_file_name}'
                    self.send_message(MessageType.TEXT, notify.encode())
                    self.gui_callback('message', notify)
                    threading.Thread(target=self._send_file_data, args=(peer_id,), daemon=True).start()

                elif msg_type == MessageType.FILE_DECLINE:
                    decliner = self.peer_nicks.get(peer_id, '?')
                    notify = f'Пользователь {decliner} отклонил файл {self.pending_file_name}'
                    self.send_message(MessageType.TEXT, notify.encode())
                    self.gui_callback('message', notify)

                elif msg_type == MessageType.FILE_DATA:
                    info = self.current_files.get(peer_id)
                    if info:
                        info['file'].write(data)
                        info['received'] += len(data)
                        if info['received'] >= info['size']:
                            info['file'].close()
                            path = info['path']
                            self.gui_callback('message', f'Файл сохранён по: {path}')
                            del self.current_files[peer_id]

                elif msg_type == MessageType.CLEAR_HISTORY:
                    with self.lock:
                        self.chat_history.clear()
                    self.gui_callback('clear_history', None)
                    notify = 'История чата была очищена'
                    self.chat_history.append(notify)
                    self.gui_callback('message', notify)

                elif msg_type == MessageType.NICK:
                    newnick = data.decode()
                    with self.lock:
                        self.peer_nicks[peer_id] = newnick
                    self.send_peer_list()
                    self.gui_callback('update_peers', self.get_peer_list())

                elif msg_type == MessageType.PEER_LIST:
                    peers = json.loads(data.decode())
                    gui_peers = [
                        {'address': f"{p['host']}:{p['port']}", 'nick': p['nick']}
                        for p in peers
                    ]
                    self.gui_callback('update_peers', gui_peers)

                else:
                    continue

        finally:
            if peer_id in self.peers:
                self.remove_peer(peer_id)


    def send_peer_list(self, conn=None):
        '''Отправляет список пиров.'''
        with self.lock:
            peers = [
                {'host': addr[0], 'port': addr[1], 'nick': self.peer_nicks.get(pid, '')}
                for pid, (_, addr) in self.peers.items()
            ]
            peers.append({'host': self.server_ip, 'port': self.port, 'nick': self.nickname})
        data = json.dumps(peers).encode()
        self.send_message(MessageType.PEER_LIST, data, conn)

    def get_peer_list(self):
        '''Возвращает список пиров.'''
        with self.lock:
            lst = [
                {'address': f"{addr[0]}:{addr[1]}", 'nick': self.peer_nicks.get(pid, f'User_{addr[1]}')}
                for pid, (_, addr) in self.peers.items()
            ]
        lst.append({'address': f"{self.server_ip}:{self.port}", 'nick': self.nickname})
        return lst

    def connect_to_peer(self, host, port):
        """Подключается к пиру: handshake → отправка истории → handle_messages."""
        if host == self.server_ip and port == self.port:
            return False
        if self.is_connected_to(host, port):
            return False

        try:
            self.connected_host = (host, port)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, port))
            self.is_host = False

            info = json.dumps({
                'conn_id': self.connection_id,
                'nickname': self.nickname,
                'listen_port': self.port
            }).encode()
            self.send_message(MessageType.CONNECTION_ID, info, sock)

            header = receive_all(sock, 8)
            if not header:
                sock.close()
                return False
            mt = MessageType(header[:4].decode())
            ln = int.from_bytes(header[4:8], 'big')
            data = receive_all(sock, ln)
            if mt != MessageType.CONNECTION_ID or not data:
                sock.close()
                return False

            resp = json.loads(data.decode())
            peer_id   = resp['conn_id']
            peer_nick = resp.get('nickname', '')
            peer_port = resp.get('listen_port', port)

            with self.lock:
                if peer_id in self.peers:
                    sock.close()
                    return False
                self.peers[peer_id] = (sock, (host, peer_port))
                self.connection_map[(host, peer_port)] = peer_id
                self.peer_nicks[peer_id] = peer_nick

            self.gui_callback('message', f'{peer_nick} подключился')
            self.chat_history.append(f'{peer_nick} подключился')
            self.send_peer_list(sock)
            self.gui_callback('update_peers', self.get_peer_list())

            threading.Thread(
                target=self.handle_messages,
                args=(sock, peer_id, (host, peer_port)),
                daemon=True
            ).start()

            return True

        except Exception as e:
            if self.debug:
                print(f"[DEBUG] connect_to_peerа ошибка {host}:{port}: {e}")
            return False
        

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


    def send_text(self, text):
        '''Отправляет текстовое сообщение.'''
        self.chat_history.append(f'{self.nickname}: {text}')
        self.send_message(MessageType.TEXT, f'{self.nickname}: {text}'.encode())

    def send_file(self, file_path):
        '''Инициирует передачу файла с запросом.'''
        try:
            size = os.path.getsize(file_path)
            name = os.path.basename(file_path)
            meta = json.dumps({'name': name, 'size': size}).encode()
            self.pending_file = file_path
            self.pending_file_name = name
            self.send_message(MessageType.FILE_META, meta)
            return True
        except Exception as e:
            print(f'Ошибка send_file: {e}')
            return False

    def _send_file_data(self, peer_id):
        '''Отправляет данные файла после принятия.'''
        sock, _ = self.peers[peer_id]
        with open(self.pending_file, 'rb') as f:
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                self.send_message(MessageType.FILE_DATA, chunk, sock)

    def respond_file(self, peer_id, accept):
        '''Вызывается GUI: принять или отклонить файл.'''
        sock, _ = self.peers[peer_id]
        if accept:
            info = self.current_files.get(peer_id)
            info['path'] = info.get('path')
            self.send_message(MessageType.FILE_ACCEPT, b'', sock)
        else:
            self.send_message(MessageType.FILE_DECLINE, b'', sock)

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

    def clear_history(self):
        '''Вызывается хозяином для ручной очистки истории у всех.'''
        with self.lock:
            self.chat_history.clear()
        self.gui_callback('clear_history', None)
        hdr = MessageType.CLEAR_HISTORY.value.encode() + (0).to_bytes(4,'big')
        with self.lock:
            for _, (s, _) in list(self.peers.items()):
                try:
                    send_all(s, hdr)
                except Exception:
                    pass
        notify = 'История чата была очищена'
        with self.lock:
            self.chat_history.append(notify)
        self.gui_callback('message', notify)
    
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

    def is_connected_to(self, host, port):
        '''Проверяет наличие подключения.'''
        with self.lock:
            return (host, port) in self.connection_map
        
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


    def stop(self):
        '''Останавливает менеджер.'''
        self.running = False
        try:
            self.sock.close()
        except:
            pass
        with self.lock:
            for pid in list(self.peers.keys()):
                self.remove_peer(pid)


    def handle_peer_list(self, data):
        '''Обрабатывает список пиров: обновляем GUI и (опционально) подключаемся.'''
        try:
            peers = json.loads(data.decode())
        except Exception:
            return

        gui_peers = [
            {'address': f"{p['host']}:{p['port']}", 'nick': p['nick']}
            for p in peers
        ]
        self.gui_callback('update_peers', gui_peers)

        for p in peers:
             host, port = p['host'], p['port']
             if host == self.server_ip and port == self.port: continue
             if not self.is_connected_to(host, port):
                 threading.Thread(target=self.connect_to_peer,
                                  args=(host, port),
                                  daemon=True).start()