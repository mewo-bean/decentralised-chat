# src/gui.py
import os
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox

class ChatGUI:
    '''Графический интерфейс для P2P-чата.'''
    def __init__(self, network_manager, port):
        '''Инициализирует GUI.'''
        self.network = network_manager
        self.port = port
        self.root = tk.Tk()
        self.root.title(f'Децентрализованный чат (Порт: {port})')
        self.root.geometry('800x600')
        self.root.protocol('WM_DELETE_WINDOW', self.on_closing)

        style = ttk.Style()
        style.configure('TButton', padding=6)
        style.configure('TFrame', background='#f0f0f0')

        control_frame = ttk.Frame(self.root)
        control_frame.pack(fill=tk.X, padx=10, pady=5)

        self.clear_button = ttk.Button(control_frame, text='Очистить историю', command=self.clear_history)
        if self.network.is_host:
            self.clear_button.pack(side=tk.RIGHT)
        
        ttk.Label(control_frame, text='Ник:').pack(side=tk.LEFT)
        self.nick_entry = ttk.Entry(control_frame, width=15)
        self.nick_entry.insert(0, self.network.nickname)
        self.nick_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text='Изменить', command=self.change_nick).pack(side=tk.LEFT)

        ttk.Label(control_frame, text='Подключиться:').pack(side=tk.LEFT, padx=(20, 0))
        self.host_entry = ttk.Entry(control_frame, width=12)
        self.host_entry.insert(0, 'localhost')
        self.host_entry.pack(side=tk.LEFT)
        self.port_entry = ttk.Entry(control_frame, width=5)
        self.port_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text='Подключиться', command=self.connect_to_peer).pack(side=tk.LEFT)

        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        peers_frame = ttk.LabelFrame(main_frame, text='Участники')
        peers_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))

        self.peers_tree = ttk.Treeview(peers_frame, columns=('nick'), show='tree')
        self.peers_tree.column('#0', width=150)
        self.peers_tree.heading('#0', text='Адрес')
        self.peers_tree.heading('nick', text='Ник')
        self.peers_tree.pack(fill=tk.BOTH, expand=True)

        chat_frame = ttk.LabelFrame(main_frame, text='Чат')
        chat_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.chat_area = scrolledtext.ScrolledText(chat_frame, state=tk.DISABLED)
        self.chat_area.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        input_frame = ttk.Frame(chat_frame)
        input_frame.pack(fill=tk.X, padx=5, pady=5)

        self.message_entry = ttk.Entry(input_frame)
        self.message_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.message_entry.bind('<Return>', lambda e: self.send_text())

        ttk.Button(input_frame, text='Отправить', command=self.send_text).pack(side=tk.LEFT)
        ttk.Button(input_frame, text='Файл', command=self.select_file).pack(side=tk.LEFT, padx=(5, 0))

        self.update_peers_list(self.network.get_peer_list())
        self.add_message(f'Вы подключены на порту: {port}')

    def change_nick(self):
        new_nick = self.nick_entry.get()
        if new_nick:
            self.network.change_nickname(new_nick)
            self.add_message(f'Ваш ник изменен на: {new_nick}')

    def connect_to_peer(self):
        host = self.host_entry.get()
        try:
            port = int(self.port_entry.get())
            if self.network.connect_to_peer(host, port):
                self.add_message(f'Подключено к {host}:{port}')
                self.clear_button.pack_forget()
            else:
                messagebox.showerror('Ошибка', f'Не удалось подключиться к {host}:{port}')
        except ValueError:
            messagebox.showerror('Ошибка', 'Некорректный порт')

    def select_file(self):
        path = filedialog.askopenfilename()
        if path and self.network.send_file(path):
            self.add_message(f'Запрос отправки файла: {os.path.basename(path)}')

    def send_text(self):
        text = self.message_entry.get()
        if text:
            self.network.send_text(text)
            self.add_message(f'Вы: {text}')
            self.message_entry.delete(0, tk.END)

    def add_message(self, message):
        self.root.after(0, self._add_message_threadsafe, message)

    def _add_message_threadsafe(self, message):
        self.chat_area.config(state=tk.NORMAL)
        self.chat_area.insert(tk.END, message + '\n')
        self.chat_area.config(state=tk.DISABLED)
        self.chat_area.yview(tk.END)

    def update_peers_list(self, peers):
        '''Запускается из потока NetworkManager: обновляет список участников.'''
        self.root.after(0, self._update_peers_list_threadsafe, peers)

    def _update_peers_list_threadsafe(self, peers):
        '''Вызывается в GUI-потоке: реальное обновление Treeview.'''
        current = {self.peers_tree.item(i)['text']: i for i in self.peers_tree.get_children()}
        for p in peers:
            addr, nick = p['address'], p['nick']
            if addr in current:
                item = current.pop(addr)
                self.peers_tree.item(item, values=(nick,))
            else:
                self.peers_tree.insert('', tk.END, text=addr, values=(nick,))
        # 2) Удаляем тех, кого уже нет
        for addr, item in current.items():
            self.peers_tree.delete(item)


    def callback_handler(self, event, data):
        if event == 'message':
            self.add_message(data)
        elif event == 'update_peers':
            self.update_peers_list(data)
        elif event == 'file_request':
            peer_id, name, size = data
            nick = self.network.peer_nicks.get(peer_id, '?')
            if messagebox.askyesno('Файл', f'{nick} прислал файл "{name}" ({size} байт). Принять?'):
                self.network.respond_file(peer_id, True)
            else:
                self.network.respond_file(peer_id, False)
        elif event == 'clear_history':
            self.chat_area.config(state=tk.NORMAL)
            self.chat_area.delete('1.0', tk.END)
            self.chat_area.config(state=tk.DISABLED)
        elif event == 'debug' and self.network.debug:
            self.add_message(f'[DEBUG] {data}')

    def clear_history(self):
        if messagebox.askyesno('Очистка истории', 'Вы уверены, что хотите очистить историю чата для всех?'):
            self.network.clear_history()

    def on_closing(self):
        self.network.stop()
        self.root.destroy()

    def start(self):
        self.root.mainloop()
