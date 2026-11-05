"""Сборка мусора идёт только в главном потоке (иначе macOS закрывает приложение)."""
import gc
import threading

from ui import thread_bridge as tb


class _Root:
    def after(self, ms, fn):
        pass


def test_install_disables_auto_gc_and_collects_on_main_thread():
    was = gc.isenabled()
    try:
        tb.install(_Root())
        assert not gc.isenabled()
        seen = []

        class Obj:
            def __del__(self):
                seen.append(threading.current_thread() is threading.main_thread())

        def make_cycle():
            a, b = Obj(), Obj()
            a.o, b.o = b, a

        t = threading.Thread(target=make_cycle)
        t.start(); t.join()
        assert seen == []                       # в рабочем потоке цикл не собран
        tb._ticks = tb._GC_EVERY
        tb._poll()                              # главный поток
        assert seen and all(seen)
    finally:
        tb._root = None
        if was:
            gc.enable()
