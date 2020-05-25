import unittest
import sqlite3
from lloidbot import turnips
from lloidbot.queue_manager import QueueManager, Action, Error, Guest, Host
from datetime import datetime
import freezegun

standard_description = 'standard description'
updated_description = 'updated description'

alice = turnips.Turnip('global', 100, 'Alice', 'ALICE', 0, standard_description, None, [None]*14)
bella = turnips.Turnip('nookmart', 200, 'Bella', 'BELLA', 5, standard_description, None, [None]*14)
cally = turnips.Turnip('nookmart', 300, 'Cally', 'CALLY', 6, None, None, [None]*14)
deena = turnips.Turnip('nookmart', 400, 'Deena', 'DEENA', 7, None, None, [None]*14)

# March 24, 2020 - Tuesday
tuesday_morning = datetime(2020, 3, 24, 10, 20)
tuesday_evening = datetime(2020, 3, 24, 21, 20)
wednesday_early = datetime(2020, 3, 25, 4, 0)
saturday_evening = datetime(2020, 3, 28, 21, 30)
saturday_end = datetime(2020, 3, 28, 23, 59)
sunday_morning = datetime(2020, 3, 29, 10, 40)
sunday_evening = datetime(2020, 3, 29, 18, 40)

class TestQueueManager(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        
        self.market = turnips.StalkMarket(self.db)
        self.manager = QueueManager(self.market)
 
        t = self.market.get_all()
        
        assert len(t) == 0

    def tearDown(self):
        self.db.execute("delete from turnips")
        self.db.close()

    @freezegun.freeze_time(tuesday_morning)
    def test_declare_accepts_listing(self):
        res = self.manager.declare(alice.id, alice.name, 150, alice.dodo, alice.gmtoffset, standard_description)
        assert len(res) == 1
        action, turnip = res[0]
        assert action == Action.LISTING_ACCEPTED, action
        assert turnip.id == alice.id
        assert turnip.current_price() == 150

        t = self.market.get(alice.id)
        assert turnip.equals(t), f"{turnip} | {t}"
        assert alice.id in self.manager.hosts

    @freezegun.freeze_time(tuesday_morning)
    def test_declare_new_price_updates_listing(self):
        res = self.manager.declare(alice.id, alice.name, 150, alice.dodo, alice.gmtoffset, standard_description)
        res = self.manager.declare(alice.id, alice.name, 200, alice.dodo, alice.gmtoffset, standard_description)
        assert len(res) == 1
        action, turnip = res[0]
        assert action == Action.LISTING_UPDATED
        assert turnip.id == alice.id
        assert turnip.current_price() == 200

    @freezegun.freeze_time(tuesday_morning)
    def test_declare_new_dodo_updates_listing(self):
        res = self.manager.declare(alice.id, alice.name, 150, alice.dodo, alice.gmtoffset)

        res = self.manager.declare(alice.id, alice.name, 150, 'xdodo', alice.gmtoffset)
        assert len(res) == 1
        action, turnip = res[0]
        assert action == Action.LISTING_UPDATED
        assert turnip.dodo == 'xdodo'
        assert turnip.current_price() == 150

    @freezegun.freeze_time(tuesday_morning)
    def test_declare_new_tz_updates_listing(self):
        res = self.manager.declare(alice.id, alice.name, 150, alice.dodo, alice.gmtoffset)

        res = self.manager.declare(alice.id, alice.name, 150, alice.dodo, alice.gmtoffset+1)
        assert len(res) == 1
        action, turnip = res[0]
        assert action == Action.LISTING_UPDATED
        assert turnip.gmtoffset == alice.gmtoffset+1
        assert turnip.current_price() == 150

    @freezegun.freeze_time(tuesday_morning)
    def test_declare_no_tz_updates_listing(self):
        res = self.manager.declare(alice.id, alice.name, 150, alice.dodo, alice.gmtoffset)

        res = self.manager.declare(alice.id, alice.name, 150, 'xdodo')
        assert len(res) == 1
        action, turnip = res[0]
        assert action == Action.LISTING_UPDATED
        assert turnip.dodo == 'xdodo'
        assert turnip.current_price() == 150

    @freezegun.freeze_time(tuesday_morning)
    def test_declare_no_dodo_updates_listing(self):
        res = self.manager.declare(alice.id, alice.name, 150, alice.dodo, alice.gmtoffset)

        res = self.manager.declare(alice.id, alice.name, 250)
        assert len(res) == 1
        action, turnip = res[0]
        assert action == Action.LISTING_UPDATED
        assert turnip.current_price() == 250

    def test_visitor_request_queue(self):
        self.manager.declare(alice.id, alice.name, 150, alice.dodo, alice.gmtoffset)

        res = self.manager.visitor_request_queue(bella.id, alice.id)
        assert len(res) == 1
        action, ahead = res[0]
        assert action == Action.ADDED_TO_QUEUE
        assert len(ahead) == 0

        assert bella.id in self.manager.guests
        assert cally.id not in self.manager.guests

        res = self.manager.visitor_request_queue(cally.id, alice.id)
        assert len(res) == 1
        action, ahead = res[0]
        assert action == Action.ADDED_TO_QUEUE
        assert len(ahead) == 1
        assert ahead[0] == bella.id

        assert bella.id in self.manager.guests
        assert cally.id in self.manager.guests

    def test_visitor_cannot_double_queue(self):
        self.manager.declare(alice.id, alice.name, 150, alice.dodo, alice.gmtoffset)
        self.manager.declare(bella.id, bella.name, 200, bella.dodo, bella.gmtoffset)

        res = self.manager.visitor_request_queue(cally.id, alice.id)
        assert len(res) == 1
        action, ahead = res[0]
        assert action == Action.ADDED_TO_QUEUE
        assert len(ahead) == 0

        res = self.manager.visitor_request_queue(cally.id, bella.id)
        assert len(res) == 1
        action, status = res[0]
        assert action == Action.NOTHING
        assert status == Error.ALREADY_QUEUED, status

    def test_pop_from_queue(self):
        self.manager.declare(alice.id, alice.name, 150, alice.dodo, alice.gmtoffset)

        res = self.manager.host_next(alice.id)
        assert len(res[0]) == 2
        r, e = res[0]
        assert r == Action.NOTHING
        assert e == Error.QUEUE_EMPTY

        self.manager.visitor_request_queue(bella.id, alice.id)
        self.manager.visitor_request_queue(cally.id, alice.id)

        res = self.manager.host_next(alice.id)
        assert len(res[0]) == 3, res
        r, g, o = res[0]
        assert r == Action.POPPED_FROM_QUEUE
        assert g == bella.id
        assert o == alice.id

        self.manager.visitor_request_queue(deena.id, alice.id)[0]

        res = self.manager.host_next(alice.id)
        assert len(res[0]) == 3
        r, g, o = res[0]
        assert r == Action.POPPED_FROM_QUEUE
        assert g == cally.id
        assert o == alice.id

        res = self.manager.host_next(alice.id)
        assert len(res[0]) == 3
        r, g, o = res[0]
        assert r == Action.POPPED_FROM_QUEUE
        assert g == deena.id
        assert o == alice.id

        res = self.manager.host_next(alice.id)
        assert len(res[0]) == 2
        r, e = res[0]
        assert r == Action.NOTHING
        assert e == Error.QUEUE_EMPTY

    def test_pop_from_nonexistent_queue(self):
        self.manager.declare(alice.id, alice.name, 150, alice.dodo, alice.gmtoffset)

        res = self.manager.host_next(bella.id)
        assert len(res[0]) == 2
        r, e = res[0]
        assert r == Action.NOTHING
        assert e == Error.NO_SUCH_QUEUE, e

    def test_visitor_queues_for_nonexistent_host(self):
        self.manager.declare(alice.id, alice.name, 150, alice.dodo, alice.gmtoffset)

        res = self.manager.visitor_request_queue(1, bella.id)
        assert len(res[0]) == 2
        r, e = res[0]
        assert r == Action.NOTHING
        assert e == Error.NO_SUCH_QUEUE, e

    def test_close_queue(self):
        self.manager.declare(alice.id, alice.name, 150, alice.dodo, alice.gmtoffset)
        self.manager.visitor_request_queue(1, alice.id)
        self.manager.visitor_request_queue(2, alice.id)
        self.manager.visitor_request_queue(3, alice.id)
        self.manager.host_next(alice.id)

        assert alice.id in self.manager.hosts
        res = self.manager.close(alice.id)
        assert (Action.QUEUE_CLOSED, alice.id, [2, 3]) in res
        assert alice.id not in self.manager.hosts

    def test_visitor_status(self):
        self.manager.declare(alice.id, alice.name, 150, alice.dodo, alice.gmtoffset)
        self.manager.visitor_request_queue(1, alice.id)
        host = self.manager.hosts[alice.id]

        assert self.manager.guests[1].status == Guest.WAITING
        assert 1 in host.queue
        assert 1 not in host.outgoing_queue

        self.manager.host_next(alice.id)

        assert self.manager.guests[1].status == Guest.VISITING, self.manager.guests[1].status
        assert 1 not in host.queue
        assert 1 in host.outgoing_queue

        self.manager.visitor_done(1)

        assert 1 not in self.manager.guests
        assert 1 not in host.queue
        assert 1 not in host.outgoing_queue

if __name__ == '__main__':
    unittest.main() 
