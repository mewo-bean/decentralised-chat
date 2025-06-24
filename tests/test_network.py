# tests/test_network.py
import json
import os
import pytest
from src.network import NetworkManager, MessageType


class DummyCallback:
    def __init__(self):
        self.events = []

    def __call__(self, event, data):
        self.events.append((event, data))


@pytest.fixture
def network_manager():
    callback = DummyCallback()
    nm = NetworkManager('127.0.0.1', 0, callback, debug=False)
    yield nm
    nm.stop()


def test_send_text_and_peer_list(network_manager):
    network_manager.send_text('Hello')
    network_manager.send_peer_list()
    assert network_manager.running is True


def test_change_nickname(network_manager):
    old_nick = network_manager.nickname
    new_nick = 'Tester'
    network_manager.change_nickname(new_nick)
    assert network_manager.nickname == new_nick
    peers = network_manager.get_peer_list()
    assert any(p['nick'] == new_nick for p in peers)


def test_is_connected_to_false(network_manager):
    assert network_manager.is_connected_to('localhost', 12345) is False


def test_handle_peer_list_adds_thread(network_manager, monkeypatch):
    test_data = json.dumps([
        {'host': '127.0.0.1', 'port': 12345, 'nick': 'peer1'},
        {'host': 'localhost', 'port': 54321, 'nick': 'peer2'},
    ]).encode()

    started = []
    monkeypatch.setattr('threading.Thread',
                        lambda *args, **kwargs: started.append((args, kwargs)) or type('MockThread', (),
                                                                                       {'start': lambda s: None})())
    network_manager.handle_peer_list(test_data)
    assert len(started) == 2


def test_get_peer_list_structure(network_manager):
    peer_list = network_manager.get_peer_list()
    assert isinstance(peer_list, list)
    assert 'address' in peer_list[0] and 'nick' in peer_list[0]


def test_message_type_enum():
    assert MessageType.TEXT.value == 'TEXT'
    assert MessageType.FILE_META.value == 'FMTA'
    assert MessageType.HEARTBEAT.value == 'BEAT'


def test_connect_to_self_returns_false(network_manager):
    assert not network_manager.connect_to_peer(network_manager.host, network_manager.port)


def test_send_message_no_peers_does_not_raise(network_manager):
    network_manager.send_message(MessageType.TEXT, b'data')


def test_get_peer_list_contains_self(network_manager):
    pl = network_manager.get_peer_list()
    assert len(pl) == 1
    addr = pl[0]['address']
    assert addr.endswith(f':{network_manager.port}')
    assert pl[0]['nick'] == network_manager.nickname


def test_handle_file_meta_and_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cb = []
    nm = NetworkManager('127.0.0.1', 0, lambda e, d: cb.append((e, d)), debug=False)
    peer_id = 'peer123'
    meta = json.dumps({'name': 'f.txt', 'size': 5}).encode()
    nm.handle_file_meta(peer_id, meta)
    assert cb and cb[0][0] == 'message'
    nm.handle_file_data(peer_id, b'12')
    assert peer_id in nm.current_files
    nm.handle_file_data(peer_id, b'345')
    assert peer_id not in nm.current_files
    assert any('получен' in d.lower() for e, d in cb)

    downloads = tmp_path / 'downloads'
    files = list(downloads.iterdir())
    assert files and files[0].name.startswith('f.txt')

    nm.stop()