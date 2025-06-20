import argparse
from chat_network import NetworkManager
from chat_gui import ChatGUI
import threading

def start_gui(network_manager, port):
    gui = ChatGUI(network_manager, port)
    network_manager.gui_callback = gui.callback_handler
    gui.start()

def main():
    parser = argparse.ArgumentParser(description='Децентрализованный P2P чат')
    parser.add_argument('--port', type=int, default=9090, help='Порт для прослушивания')
    parser.add_argument('--host', type=str, default='localhost', help='Хост для прослушивания')
    parser.add_argument('--nogui', action='store_true', help='Запуск в консольном режиме')
    args = parser.parse_args()

    def temp_callback(event, data):
        print(f"Event: {event}, Data: {data}")

    network_manager = NetworkManager(args.host, args.port, temp_callback)

    if args.nogui:
        print(f"Чат запущен на {args.host}:{args.port}")
        print("Введите сообщения. Для выхода введите /exit")
        
        def console_input():
            while True:
                message = input()
                if message == "/exit":
                    network_manager.stop()
                    break
                network_manager.send_text(message)
        
        threading.Thread(target=console_input, daemon=True).start()
        network_manager.start()
    else:
        start_gui(network_manager, args.port)

if __name__ == "__main__":
    main()