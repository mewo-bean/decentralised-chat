# tests/test_network.py
import pytest
import json
from chat.network import NetworkManager

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
    network_manager.send_text("Hello")
    network_manager.send_peer_list()
    assert network_manager.running is True

def test_change_nickname(network_manager):
    old_nick = network_manager.nickname
    new_nick = "Tester"
    network_manager.change_nickname(new_nick)
    assert network_manager.nickname == new_nick
    peers = network_manager.get_peer_list()
    assert any(p['nick'] == new_nick for p in peers)

def test_is_connected_to_false(network_manager):
    assert network_manager.is_connected_to("localhost", 12345) is False

def test_handle_peer_list_adds_thread(network_manager, monkeypatch):
    test_data = json.dumps([
        {"host": "127.0.0.1", "port": 12345, "nick": "peer1"},
        {"host": "localhost", "port": 54321, "nick": "peer2"},
    ]).encode()

    started = []
    monkeypatch.setattr("threading.Thread", lambda *args, **kwargs: started.append((args, kwargs)) or type("MockThread", (), {"start": lambda s: None})())
    network_manager.handle_peer_list(test_data)
    assert len(started) == 2

def test_get_peer_list_structure(network_manager):
    peer_list = network_manager.get_peer_list()
    assert isinstance(peer_list, list)
    assert 'address' in peer_list[0] and 'nick' in peer_list[0]
