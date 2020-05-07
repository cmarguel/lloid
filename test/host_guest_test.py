import unittest
from lloidbot.queue_manager import Action, Error, Host, Guest

dodo = "xxxxx"

class HostGuestTest(unittest.TestCase):
    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_host_equal(self):
        h1 = Host(1, dodo)
        h2 = Host(1, dodo)

        assert h1 == h2

    def test_host_not_equal(self):
        h1 = Host(1, dodo)
        h2 = Host(2, dodo)

        assert h1 != h2

    def test_host_comparison_by_id(self):
        h1 = Host(1, dodo)

        assert h1 == 1
        assert h1 != 2

    def test_add_to_queue(self):
        h = Host(1, dodo)
        assert h.addToQueue(1) == (Action.ADDED_TO_QUEUE, Guest(1,h))
        assert h.addToQueue(2) == (Action.ADDED_TO_QUEUE, Guest(2,h))

        assert 1 in h.queue
        assert 2 in h.queue
        assert 3 not in h.queue

    def test_wont_add_if_already_queued(self):
        h = Host(1, dodo)
        assert h.addToQueue(1) == (Action.ADDED_TO_QUEUE, Guest(1,h))
        assert h.addToQueue(1) == (Error.ALREADY_QUEUED, Guest(1,h))

    def test_pop_from_host_queue(self):
        h = Host(1, dodo)
        assert h.addToQueue(1) == (Action.ADDED_TO_QUEUE, Guest(1,h))
        assert h.addToQueue(2) == (Action.ADDED_TO_QUEUE, Guest(2,h))

        assert len(h.outgoing_queue) == 0

        guest, e = h.pop()
        assert guest == Guest(1, h)
        assert guest.status == Guest.VISITING
        assert e is None
        assert len(h.outgoing_queue) == 1

        guest, e = h.pop()
        assert guest == Guest(2, h)
        assert guest.status == Guest.VISITING
        assert e is None
        assert len(h.outgoing_queue) == 2

        guest, e = h.pop()
        assert guest is None
        assert e is Error.QUEUE_EMPTY
        assert len(h.outgoing_queue) == 2

