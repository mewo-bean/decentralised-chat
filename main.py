import argparse
import random
import socket
import sys
import threading
import time

from src.gui import ChatGUI
from src.network import NetworkManager


def find_free_port(host, start_port=1024, end_port=65535, max_attempts=100):
    for _ in range(max_attempts):
        port = random.randint(start_port, end_port)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.bind((host, port))
                return port
        except (OSError, socket.error):
            continue
    raise RuntimeError(f'Не удалось найти свободный порт после {max_attempts} попыток')


def start_gui(network_manager, port):
    gui = ChatGUI(network_manager, port)
    network_manager.gui_callback = gui.callback_handler
    gui.start()


def main():
    parser = argparse.ArgumentParser(description='Децентрализованный чат')
    parser.add_argument('--port', type=int, default=0, help='Порт для прослушивания (0 - случайный)')
    parser.add_argument('--host', type=str, default='127.0.0.1', help='Хост для прослушивания')
    parser.add_argument('--nogui', action='store_true', help='Запуск в консольном режиме')
    parser.add_argument('--debug', action='store_true', help='Режим отладки')
    args = parser.parse_args()

    if args.port == 0:
        try:
            args.port = find_free_port(args.host)
            print(f'Выбран случайный порт: {args.port}')
        except RuntimeError as e:
            print(e)
            sys.exit(1)

    def temp_callback(event, data):
        if event == 'debug':
            print(f'[DEBUG] {data}')
        elif args.debug or event != 'heartbeat':
            print(f'Event: {event}, Data: {data}')

    try:
        network_manager = NetworkManager(args.host, args.port, temp_callback, debug=args.debug)
        print(f'Чат запущен на {args.host}:{args.port}')

        if args.nogui:
            print('Введите сообщения. Для выхода введите /exit')

            def console_input():
                while True:
                    message = input()
                    if message == '/exit':
                        network_manager.stop()
                        break
                    network_manager.send_text(message)
                    print(f'Вы: {message}')

            threading.Thread(target=console_input, daemon=True).start()
            while network_manager.running:
                time.sleep(0.5)
        else:
            start_gui(network_manager, args.port)

    except OSError as e:
        print(f'Ошибка запуска: {e}')
        sys.exit(1)
    except Exception as e:
        print(f'Критическая ошибка при запуске: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()