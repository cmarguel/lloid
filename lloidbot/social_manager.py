from lloidbot.turnips import Status
from lloidbot import queue_manager
import logging
import enum
from enum import auto
from functools import wraps
import asyncio

queue = []
queue_interval_minutes = 10
queue_interval = 60 * queue_interval_minutes
poll_sleep_interval = 5

logger = logging.getLogger('lloid')


# These actions are values that will be returned by social manager, and represent
# actions that the caller should take upon receiving the result. These actions
# should be achievable using features available on standard chat platforms, but 
# should not be specific to any chat platform.
# For instance, reactions are not available on IRC, so there should not be any
# UNREACT_GUEST action. Instead, we have UPDATE_QUEUE_INFO, which a Discord-specific
# caller may decide means removing the guest's reaction to the message, but which an 
# IRC-specific caller might implement as a message posted by the bot somewhere--or 
# even as a no-op, if it's deemed too annoying to get such updates on IRC.
class Action(enum.Enum):
    ACTION_REJECTED         = auto() # reason
    INFO                    = auto() # requested info
    CONFIRM_LISTING_POSTED  = auto() # owner_id
    POST_LISTING            = auto() # owner id, price, description, turnip.current_time()
    CONFIRM_LISTING_UPDATED = auto() # owner id
    UPDATE_LISTING          = auto() # owner_id, price, description, turnip.current_time()
    CONFIRM_QUEUED          = auto() # guest_id, owner_id, queueAhead
    WARNING_MESSAGE         = auto() # guest_id, owner_id
    BOARDING_MESSAGE        = auto() # guest_id, owner_id, dodo
    ARRIVAL_ALERT           = auto() # host id, guest id
    CONFIRM_PAUSED          = auto() # host id, [remaining guests]
    CONFIRM_RESUMED         = auto() # host id, [remaining guests]
    CONFIRM_CLOSED          = auto() # host id, [remaining guests]
    APOLOGY_CLOSED          = auto() # guest_id, host id
    INVALID_DONE            = auto() # guest_id, host id # This is for when they say done, but are not in a state where `done` makes sense; eg: they are still in line.
    THANKS_DONE             = auto() # guest_id
    THANKS_BUT_PAUSED       = auto() # guest_id
    THANKS_BUT_CLOSED       = auto() # guest_id

# This might seem redundant, but the intention here is for me to not screw up the payload,
# by forcing the interpreter to catch when I forgot or included too many arguments.
class ResultBuilder:
    def __init__(self):
        self.out = []
    
    def add(self, *args):
        self.out += [args]

    def action_rejected(self, reason):
        self.add(Action.ACTION_REJECTED, reason)

    def info(self, requestedInfo):
        self.add(Action.INFO, requestedInfo)
    
    def confirm_listing_posted(self, owner_id):
        self.add(Action.CONFIRM_LISTING_POSTED, owner_id)
    
    def post_listing(self, owner_id, price, description, current_time):
        self.add(Action.POST_LISTING, owner_id, price, description, current_time)

    def confirm_listing_updated(self, owner_id):
        self.add(Action.CONFIRM_LISTING_UPDATED, owner_id)

    def update_listing(self, owner_id, price, description, current_time):
        self.add(Action.UPDATE_LISTING, owner_id, price, description, current_time)
    
    def confirm_queued(self, guest_id, owner_id, queueAhead):
        self.add(Action.CONFIRM_QUEUED, guest_id, owner_id, queueAhead)

    def warning_message(self, guest_id, owner_id):
        self.add(Action.WARNING_MESSAGE, guest_id, owner_id)

    def boarding_message(self, guest_id, owner_id, dodo):
        self.add(Action.BOARDING_MESSAGE, guest_id, owner_id, dodo)

    def arrival_alert(self, host_id, guest_id):
        self.add(Action.ARRIVAL_ALERT, host_id, guest_id)

    def confirm_closed(self, host_id, remaining_guests):
        self.add(Action.CONFIRM_CLOSED, host_id, remaining_guests)

    def apology_closed(self, guest_id, host_id):
        self.add(Action.APOLOGY_CLOSED, guest_id, host_id)

    def thanks_done(self, guest_id):
        self.add(Action.THANKS_DONE, guest_id)

    def thanks_but_closed(self, guest_id):
        self.add(Action.THANKS_BUT_CLOSED, guest_id)

    def thanks_but_paused(self, guest_id):
        self.add(Action.THANKS_BUT_PAUSED, guest_id)

    def invalid_done(self, guest_id, host_id):
        self.add(Action.INVALID_DONE, guest_id, host_id)

    def confirm_paused(self, host_id, guests):
        self.add(Action.CONFIRM_PAUSED, host_id, guests)

    def confirm_resumed(self, host_id, guests):
        self.add(Action.CONFIRM_RESUMED, host_id, guests)

def reports_results(fn):
    @wraps(fn)
    def decorator(*args, **kwargs):
        builder = ResultBuilder()
        fn(args[0], builder, *args[1:], **kwargs)
        return builder.out

    return decorator

# This class should manage the queuing on the abstract idea of a social platform 
# (discord, IRC, etc). Currently, we assume a Discord-like featureset, but we
# should make sure to handle cases where the platform doesn't support things--eg:
# IRC won't let you delete messages, or react to them.
# This should map actions taken on a platform (eg: reaction) to the command the 
# action is intended to represent (eg: queue up).
# We'll figure this next part out later, but this class may not actually belong 
# here as in the ideal case, the bot shouldn't have to wait for the caller to provide
# it with a message id--which it needs to perform its duty.
#
# It should receive actions from the queue manager and translate them into message actions
# that the caller can perform. 
# eg: call_next -> host_next -> (CODE_DISPENSED) -> 
#     return [SEND_CODE (to guest), SEND_WARNING (to next guest), SEND_NOTIFICATION (to host)]
class SocialManager:
    def __init__(self, queueManager):
        self.queueManager = queueManager

    @reports_results
    def post_listing(self, output, user_id, name, price, description=None, dodo=None, tz=None, chan=None):
        res = self.queueManager.declare(user_id, name, price, dodo, tz, description)
        for r in res:
            status, *params = r
            if status == queue_manager.Action.LISTING_ACCEPTED:
                turnip = params[0]
                output.confirm_listing_posted(user_id)
                output.post_listing(user_id, price, description, turnip.current_time())
            elif status == queue_manager.Action.LISTING_UPDATED:
                turnip = params[0]
                output.confirm_listing_updated(user_id)
                output.update_listing(turnip.id, turnip.current_price(), turnip.description, turnip.current_time())
            elif status == queue_manager.Action.NOTHING:
                output.action_rejected(params[0])
            else:
                logger.warning(f"""Posting the following listing resulted in a status of {status.name}. """
                                f"""Arguments given to the listing were: {user_id} | {name} | {description} | {price} | {dodo} | {tz} | {chan} """) 

    def register_message(self, user_id, message_id):
        pass

    @reports_results
    def guest_done(self, output, guest_id):
        res = self.queueManager.visitor_done(guest_id)
        error = next((r for r in res if r[0] == queue_manager.Action.NOTHING), None)
        if error is not None:
            _, err = error
            if err == queue_manager.Error.QUEUE_PAUSED:
                output.thanks_but_paused(guest_id)

        popped = next((r for r in res if r[0] == queue_manager.Action.POPPED_FROM_QUEUE), None)
        if popped is not None:
            _, _, host = popped
            self.process_guests(output, res, host.id)
            output.thanks_done(guest_id)
        else:
            output.thanks_but_closed(guest_id)

    @reports_results
    def host_next(self, output, host_id):
        next_batch = self.queueManager.host_next(host_id)
        resumed = next((r for r in next_batch if r[0] == queue_manager.Action.DISPENSING_REACTIVATED), None)
        if resumed is not None:
            queue = resumed[1]
            output.confirm_resumed(host_id, queue)

        failed = next((r for r in next_batch if r[0] == queue_manager.Action.NOTHING), None)
        if failed is not None:
            output.action_rejected(failed[1])
        else:
            self.process_guests(output, next_batch, host_id)

    def process_guests(self, output, next_batch, host_id):
        # Currently next batch is just one person, but in the future we may accomodate more per `next`.
        for res in next_batch:
            st = res[0]
            if st == queue_manager.Action.POPPED_FROM_QUEUE:
                guest, owner = res[1:]
                output.arrival_alert(owner.id, guest.id)
                output.boarding_message(guest.id, owner.id, owner.dodo)

                st, q = self.queueManager.get_queue_for(host_id)
                if st == queue_manager.Action.INFO and len(q) > 0:
                    output.warning_message(q[0].id, owner.id)
    
    # This will eventually be obsoleted. See comment in queueManager.get_turnip
    def get_turnip(self, host_id):
        return self.queueManager.get_turnip(host_id)

    @reports_results
    def host_close(self, output, host_id):
        res = self.queueManager.close(host_id)
        for r in res:
            st = r[0]
            if st == queue_manager.Action.NOTHING:
                output.action_rejected(r[1])
            if st == queue_manager.Action.QUEUE_CLOSED:
                host, remainder = r[1:]
                output.confirm_closed(host, remainder)
                for guest in remainder:
                    output.apology_closed(guest.id, host)

    @reports_results
    def reaction_added(self, output, user_id, host_id):
        res = self.queueManager.visitor_request_queue(user_id, host_id)
        for action, p in res:
            if action == queue_manager.Action.ADDED_TO_QUEUE:
                output.confirm_queued(user_id, host_id, p)

    @reports_results
    def host_pause(self, output, host_id):
        r = self.queueManager.host_pause(host_id)
        if r[0][0] == queue_manager.Action.DISPENSING_BLOCKED:
            output.confirm_paused(host_id, self.queueManager._queue(host_id))

class TimedActions(enum.Enum):
    CREATE_TIMER = auto() # key, length_seconds, post-timer callback
    CANCEL_TIMER = auto() # key

class TimedSocialManager(SocialManager):
    def __init__(self, queueManager):
        SocialManager.__init__(self, queueManager)

        self.guest_timers = {} # guests -> timers

    def guest_loop(self, guest_id, owner_id):
        pass

    def guest_timed_out(self, guest_id):
        pass

    def post_listing(self, user_id, name, price, description=None, dodo=None, tz=None, chan=None):
        res = super().post_listing(user_id, name, price, description, dodo, tz, chan)

        return res

    def host_requested_pause(self, owner_id):
        pass

    def host_requested_next(self, owner_id):
        pass

    def reaction_added(self, output, user_id, host_id):
        res = super().reaction_added(output, user_id, host_id)

        return res

