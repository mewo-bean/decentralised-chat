import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import os

class ChatGUI:
    def __init__(self, network_manager, port):
        self.network = network_manager
        self.port = port
        self.root = tk.Tk()
        self.root.title(f"Децентрализованный чат (Порт: {port})")
        self.root.geometry("800x600")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        style = ttk.Style()
        style.configure("TButton", padding=6)
        style.configure("TFrame", background="#f0f0f0")

        control_frame = ttk.Frame(self.root)
        control_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(control_frame, text="Ник:").pack(side=tk.LEFT)
        self.nick_entry = ttk.Entry(control_frame, width=15)
        self.nick_entry.insert(0, self.network.nickname)
        self.nick_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Изменить", command=self.change_nick).pack(side=tk.LEFT)

        ttk.Label(control_frame, text="Подключиться:").pack(side=tk.LEFT, padx=(20, 0))
        self.host_entry = ttk.Entry(control_frame, width=12)
        self.host_entry.insert(0, "localhost")
        self.host_entry.pack(side=tk.LEFT)
        self.port_entry = ttk.Entry(control_frame, width=5)
        self.port_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Подключиться", command=self.connect_to_peer).pack(side=tk.LEFT)

        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        peers_frame = ttk.LabelFrame(main_frame, text="Участники")
        peers_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))

        self.peers_tree = ttk.Treeview(peers_frame, columns=("nick"), show="tree")
        self.peers_tree.column("#0", width=150)
        self.peers_tree.heading("#0", text="Адрес")
        self.peers_tree.heading("nick", text="Ник")
        self.peers_tree.pack(fill=tk.BOTH, expand=True)

        chat_frame = ttk.LabelFrame(main_frame, text="Чат")
        chat_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.chat_area = scrolledtext.ScrolledText(chat_frame, state=tk.DISABLED)
        self.chat_area.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        input_frame = ttk.Frame(chat_frame)
        input_frame.pack(fill=tk.X, padx=5, pady=5)

        self.message_entry = ttk.Entry(input_frame)
        self.message_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.message_entry.bind("<Return>", lambda e: self.send_text())

        ttk.Button(input_frame, text="Отправить", command=self.send_text).pack(side=tk.LEFT)
        ttk.Button(input_frame, text="Файл", command=self.send_file).pack(side=tk.LEFT, padx=(5, 0))

        self.update_peers_list(self.network.get_peer_list())
        self.add_message(f"Вы подключены на порту: {port}")

    def change_nick(self):
        new_nick = self.nick_entry.get()
        if new_nick:
            self.network.change_nickname(new_nick)
            self.add_message(f"Ваш ник изменен на: {new_nick}")

    def connect_to_peer(self):
        host = self.host_entry.get()
        try:
            port = int(self.port_entry.get())
            if self.network.connect_to_peer(host, port):
                self.add_message(f"Подключено к {host}:{port}")
            else:
                messagebox.showerror("Ошибка", f"Не удалось подключиться к {host}:{port}")
        except ValueError:
            messagebox.showerror("Ошибка", "Некорректный порт")

    def send_text(self):
        text = self.message_entry.get()
        if text:
            try:
                self.network.send_text(text)
                self.add_message(f"Вы: {text}")
                self.message_entry.delete(0, tk.END)
            except Exception as e:
                self.add_message(f"Ошибка отправки: {str(e)}")

    def send_file(self):
        file_path = filedialog.askopenfilename()
        if file_path:
            try:
                if self.network.send_file(file_path):
                    self.add_message(f"Вы отправили файл: {os.path.basename(file_path)}")
            except Exception as e:
                self.add_message(f"Ошибка отправки файла: {str(e)}")

    def add_message(self, message):
        self.root.after(0, self._add_message_threadsafe, message)
    
    def _add_message_threadsafe(self, message):
        self.chat_area.config(state=tk.NORMAL)
        self.chat_area.insert(tk.END, message + "\n")
        self.chat_area.config(state=tk.DISABLED)
        self.chat_area.yview(tk.END)

    def update_peers_list(self, peers):
        self.root.after(0, self._update_peers_list_threadsafe, peers)
    
    def _update_peers_list_threadsafe(self, peers):
        current_items = {}
        for item in self.peers_tree.get_children():
            addr = self.peers_tree.item(item)['text']
            current_items[addr] = item
        
        existing_items = set(self.peers_tree.get_children())
        
        for peer in peers:
            addr = peer['address']
            if addr in current_items:
                item = current_items[addr]
                if item in existing_items:
                    self.peers_tree.item(item, values=(peer['nick'],))
            else:
                new_item = self.peers_tree.insert("", tk.END, text=addr, values=(peer['nick'],))
                current_items[addr] = new_item
        
        current_addrs = [peer['address'] for peer in peers]
        for addr, item in current_items.items():
            if addr not in current_addrs and item in existing_items:
                try:
                    self.peers_tree.delete(item)
                except:
                    pass

    def on_closing(self):
        self.network.stop()
        self.root.destroy()

    def start(self):
        self.root.mainloop()

    def callback_handler(self, event, data):
        if event == "message":
            self.add_message(data)
        elif event == "update_peers":
            self.update_peers_list(data)
        elif event == "debug" and self.network.debug:
            self.add_message(f"[DEBUG] {data}")