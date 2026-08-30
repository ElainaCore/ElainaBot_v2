import psutil

from core.base import restart


class FakeProcess:
    def __init__(self, pid):
        self.pid = pid
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_stop_child_processes_terminates_tree_and_kills_survivors(monkeypatch):
    children = [FakeProcess(101), FakeProcess(202)]
    parent = type('Parent', (), {'children': lambda self, recursive: children})()
    waits = []

    monkeypatch.setattr(restart.psutil, 'Process', lambda: parent)

    def wait_procs(processes, timeout):
        waits.append(timeout)
        return (processes[:1], processes[1:]) if len(waits) == 1 else (processes, [])

    monkeypatch.setattr(restart.psutil, 'wait_procs', wait_procs)

    assert restart.stop_child_processes() == [101, 202]
    assert all(process.terminated for process in children)
    assert not children[0].killed
    assert children[1].killed
    assert waits == [3, 1]


def test_stop_child_processes_ignores_process_lookup_error(monkeypatch):
    monkeypatch.setattr(restart.psutil, 'Process', lambda: (_ for _ in ()).throw(psutil.NoSuchProcess(1)))

    assert restart.stop_child_processes() == []
