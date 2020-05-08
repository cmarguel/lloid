import inspect
import discord
import enum
from discord.utils import get
from discord.ext import commands
import sqlite3
from lloidbot import turnips, queue_manager, social_manager
import asyncio
import sys
from dotenv import load_dotenv
import os
import re
import sentry_sdk
import logging
import argparse
import typing
from functools import wraps, partial

queue = []
queue_interval_minutes = 10
queue_interval = 60 * queue_interval_minutes
poll_sleep_interval = 5
logger = logging.getLogger('lloid')

messages = {
    social_manager.Action.CONFIRM_LISTING_POSTED:
        "Okay! Please be responsible and message \"**close**\" to indicate when you've closed. "
        "You can update the dodo code with the normal syntax. \n\n"
        "Messaging me \"**pause**\" will extend the cooldown timer by {cooldown} "
        "minutes each time. This stacks, so if you want me to wait {double_cooldown} minutes, "
        "just message me pause twice, and so on.\n\n"
        "You can also let the next person in and reset the timer to normal by messaging me \"**next**\".\n"
        "To edit the listing, simply send the same command with the updated info. If all you're changing is "
        "your dodo code, `host price xdodo` will suffice. Nobody will have to requeue to receive updated codes, "
        "but they'll have to reach out to you if you changed your code after they received an old one.",
    social_manager.Action.POST_LISTING:
        ">>> **{name}** has turnips selling for **{current_price}**. "
        'Local time: **{current_time}**. '
        "React to this message with 🦝 to be queued up for a code. {desc}",
    social_manager.Action.CONFIRM_LISTING_UPDATED: 
        "Updated your info. Anyone still in line will get the updated codes.",
    social_manager.Action.CONFIRM_QUEUED:
        "Queued you up for a dodo code for {owner_name}. Estimated time: Anywhere between {interval_s}-{interval_e} minutes. "
        "This is affected by several factors, but barring the host explicitly pausing, each person will be waiting at most {queue_interval} minutes. "
        "If you want to queue up elsewhere, or if you have to go, just unreact and it'll free you up.\n\n"
        "In the meantime, please be aware of common courtesy--**if you leave the island, please requeue if you plan to come back for any reason!** "
        "Also, a lot of people might be ahead of you, so **go in, do the one thing you're there for, and leave**. "
        "If you're there to sell turnips, don't look for Saharah or shop at Nook's! And please, **DO NOT USE the minus (-) button to exit!** "
        "There are reports that exiting via minus button can result in people getting booted without their loot getting saved, and even save corruption. Use the airport!",
    social_manager.Action.BOARDING_MESSAGE:
        "\n\n⭐⭐⭐ **NOW BOARDING** ⭐⭐⭐\n\n"

        "Hope you enjoy your trip to **{owner_name}**'s island! Be polite, observe "
        "social distancing, leave a tip if you can, and **please be responsible and "
        "message me \"done\" when you've left (unless the island already has a lot "
        "of visitors inside, in which case... don't bother)**. Doing this lets the next "
        "visitor in. The Dodo code is **{dodo}**.",
    social_manager.Action.WARNING_MESSAGE:
        "\n\n⚠️⚠️⚠️\n"
        "Your flight to {owner_name}'s island is boarding soon! Please have your tickets ready, we'll be calling you forward some time in the next 0-{max_wait} minutes!\n{addendum}",
    social_manager.Action.ARRIVAL_ALERT:
        "Sure thing, friend! Next in line is **{guest_name}**; let's prepare to give them a nice warm welcome!",
    social_manager.Action.CONFIRM_CLOSED:
        "Thanks for responsibly closing your doors, pal! I'll send my apologies to the **{num_queued}** people left in line!",
    social_manager.Action.APOLOGY_CLOSED:
        "Hey pal, my apologies, but it looks like **{owner_name}** closed up shop!"
}

errors = {
    turnips.Status.DODO_REQUIRED: 
        "This seems to be your first time setting turnips, so you'll need to provide "
        "both a dodo code and a GMT offset (just a positive or negative integer). "
        "The price can be a placeholder if you want.",
    turnips.Status.TIMEZONE_REQUIRED: 
        "This seems to be your first time setting turnips, so you'll need to provide "
        "both a dodo code and a GMT offset (just a positive or negative integer). "
        "The price can be a placeholder if you want.",
    turnips.Status.PRICE_REQUIRED:
        "You'll need to tell us how much the turnips are at least.",
    # look into this, it may have the same error values as something else
    queue_manager.Error.ALREADY_QUEUED:
        "You seem to already be in line somewhere, friend.",
    queue_manager.Error.QUEUE_EMPTY:
        "Nobody's in the queue, chum!",
    queue_manager.Error.NO_SUCH_QUEUE:
        "Sorry bud, there must be some kind of mistake--I don't remember you opening up your island!",
    turnips.Status.DODO_INCORRECT_FORMAT: 
        "This dodo code appears to be invalid. Please make sure to check the length and characters used."
}

class GeneralCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def queueinfo(self, ctx):
        guest = ctx.message.author.id
        if guest not in self.bot.market.queue.requesters:
            await ctx.send("You don't seem to be queued up for anything. "
            "It could also be that the code got sent to you just now. Please check your DMs.")
            return
        owner = self.bot.market.queue.requesters[guest]
        q = self.bot.market.queue.queues[owner]
        qsize = len(q)
        index = -1
        try:
            index = [qq[0] for qq in q].index(guest)
        except:
            pass

        if index < 0:
            await ctx.send("You don't seem to be queued up for anything.")
        else:
            index += 1
            await ctx.send(f"Your position in the queue is {index} in a queue of {qsize} people. Position 1 means you're next (you'll get another DM when you reach this position).")
            if owner in self.bot.is_paused and self.bot.is_paused[owner]:
                wait = (1+self.bot.requested_pauses[owner])*queue_interval_minutes
                await ctx.send(f"Just so you know, the host asked me to hold off on giving out codes for roughly another {wait} minutes or so, so don't be surprised if your queue number doesn't change for a while. "
                    "They can cancel this waiting period at any time, so you won't necessarily be waiting that long.")

# This decorator denotes that the decorated method returns a list of 
# messages, in the form [(channel, key, *params, *on_sent, *on_fail)]. 
# The channel is any object that contains an async send(str) method, 
# which will be used to send the message. The key corresponds to a message
# that is defined in either the global `message` dict or the global `errors`
# dict, in that order of priority.
# The optional params argument is a dict of the keywords that are required 
# to format the message. 
# The optional on_sent argument is an async callback that is executed upon success
# of the message sending. It passes the Discord message object, as well as
# the message tuple that was just processed.
# The optional on_fail argument is an async callback that is executed when an exception
# occurs during message sending--for instance, if the channel blocks messages.
# It passes the error as well as the message tuple that errored out.
# All messages in the list are guaranteed to be processed by Lloid in order,
# although variable issues such as users' internet speeds and Discord's backend
# mean that near-simultaneous messages may not be received in order.
def sends_messages(fn):
    @wraps(fn)
    async def decorator(*args, **kwargs):
        messages_to_send = await fn(*args, **kwargs)

        for message in messages_to_send:
            channel, message_key = message[:2]
            if isinstance(channel, discord.Message):
                continue
            message_params = {}
            on_sent = None
            on_fail = None
            if len(message) > 2:
                message_params = message[2]
            if len(message) > 3:
                on_sent = message[3]
            if len(message) > 4:
                on_fail = message[4]

            try:
                msg = None
                if message_key in messages:
                    msg = await channel.send(messages[message_key].format(**message_params))
                elif message_key in errors: 
                    msg = await channel.send(errors[message_key].format(**message_params))
                else:
                    logger.warning(f"Tried to send message, but key was not found: {message_key}")
                if on_sent is not None:
                    await on_sent(msg, message)
            except Exception as err:
                if on_fail is not None:
                    await on_fail(err, message)
        return messages_to_send

    return decorator

# Similar to sends_messages, but takes a message object to edit instead of a channel to send through.
def edits_messages(fn):
    @wraps(fn)
    async def decorator(*args, **kwargs):
        messages_to_send = await fn(*args, **kwargs)

        for message in messages_to_send:
            msg, message_key = message[:2]
            if not isinstance(msg, discord.Message):
                continue
            message_params = {}
            on_sent = None
            on_fail = None
            if len(message) > 2:
                message_params = message[2]
            if len(message) > 3:
                on_sent = message[3]
            if len(message) > 4:
                on_fail = message[4]

            try:
                if message_key in messages:
                    await msg.edit(content=messages[message_key].format(**message_params))
                else:
                    logger.warning(f"Tried to edit message, but key was not found: {message_key}")
                if on_sent is not None:
                    await on_sent(msg, message)
            except Exception as err:
                if on_fail is not None:
                    await on_fail(err, message)
        return messages_to_send

    return decorator
        
class DMCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    def cog_check(self, ctx):
        # only allow DMs
        return not ctx.message.guild

    @commands.command()
    @sends_messages
    async def close(self, ctx):
        res = self.bot.social_manager.host_close(ctx.author.id)
        messages = []
        for r in res:
            st = r[0]
            if st == social_manager.Action.ACTION_REJECTED:
                return [(ctx, r[1])]
            elif st == social_manager.Action.CONFIRM_CLOSED:
                host_id, remaining = r[1:]
                num_queued = len(remaining)
                messages += [(ctx, st, locals())]
            elif st == social_manager.Action.APOLOGY_CLOSED:
                guest_id, host_id = r[1:]
                owner_name = self.bot.get_user(host_id).name
                messages += [(self.bot.get_user(guest_id), st, locals())]
        return messages

    @commands.command()
    async def done(self, ctx):
        guest = ctx.author.id
        owner = self.bot.recently_departed.pop(guest, None)

        if owner in self.bot.is_paused and self.bot.is_paused[owner]:
            await ctx.send("Thanks for the heads-up! "
            "The queue is actually paused at the moment, so the host will be the one to let the next person in.")
            return
        if owner is not None and owner in self.bot.sleepers:
            logger.info("Visitor done, cancelling timer")
            self.bot.sleepers[owner].cancel()
            logger.info("Timer cancelled, thanking visitor")
            await ctx.send("Thanks for the heads-up! Letting the next person in now.")
        elif owner is not None:
            owner_name = self.bot.get_user(owner).name
            logger.info(f"Visitor marked themselves as done, but owner {owner_name} was not in sleepers")
            await ctx.send("Thanks for the heads-up! Letting the next person in now.")

    @commands.command()
    @sends_messages
    async def next(self, ctx):
        messages = []
        res = self.bot.social_manager.host_next(ctx.author.id)
        for r in res:
            st = r[0]
            if st == social_manager.Action.BOARDING_MESSAGE:
                guest_id, host_id, dodo = r[1:]
                guest = self.bot.get_user(guest_id)
                owner_name = self.bot.get_user(host_id).name

                messages += [(guest, st, locals())]

                try:
                    await self.bot.associated_message[host_id].remove_reaction('🦝', guest)
                except Exception as ex:
                    logger.warning("Couldn't remove reaction; error: %s" % ex)
            elif st == social_manager.Action.ARRIVAL_ALERT:
                host_id, guest_id = r[1:]
                guest_name = self.bot.get_user(guest_id).name

                messages += [(ctx, st, locals())]
            elif st == social_manager.Action.WARNING_MESSAGE:
                guest_id, host_id = r[1:]
                max_wait = queue_interval_minutes
                owner_name = self.bot.get_user(host_id).name

                addendum = ""
                turnip = self.bot.social_manager.get_turnip(host_id)
                if turnip.description is not None:
                    addendum = f"By the way, here's the current description of the island, in case you need a review or in case it's been updated since you last viewed the listing: {turnip.description}",
                messages += [(self.bot.get_user(guest_id), st, locals())]
            else:
                messages += [(ctx, r[1])]
        return messages
    
    @commands.command()
    async def pause(self, ctx):
        if ctx.author.id in self.bot.market.queue.queues:
            if self.bot.market.has_listing(ctx.author.id):
                await ctx.send(f"Okay, extending waiting period by another {queue_interval // 60} minutes. "
                "You can cancel this by letting the next person in with **next**.\n")
                self.bot.is_paused[ctx.author.id] = True
                if ctx.author.id not in self.bot.requested_pauses:
                    self.bot.requested_pauses[ctx.author.id] = 0
                self.bot.requested_pauses[ctx.author.id] += 1
                return
            else:
                await ctx.send("If you want to move to the back of the line, unqueue and requeue. "
                "If you think the island is congested, please tell the host to pause with the same command you just sent.")
    
    @commands.command(name='host')
    @sends_messages
    @edits_messages
    async def host(self, ctx, price: int, dodo, tz: typing.Optional[int], *, description = None):
        # This check can probably be converted into a discord.py command check, but it's only used for one command at the moment.
        if not re.match(r'[A-HJ-NP-Y0-9]{5}', dodo, re.IGNORECASE):
            return [(ctx, turnips.Status.DODO_INCORRECT_FORMAT)]

        actions = self.bot.social_manager.post_listing(ctx.author.id, ctx.author.name, price, description, dodo, tz)
        to_send = []
        for action in actions:
            status = action[0]
            if status == social_manager.Action.CONFIRM_LISTING_POSTED:
                cooldown = queue_interval_minutes
                double_cooldown = queue_interval_minutes*2
                to_send += [(ctx, status, locals())]
            elif status == social_manager.Action.POST_LISTING:
                owner_id, price, description, time = action[1:]
                
                turnip = self.bot.market.get(ctx.author.id)
                name = turnip.name
                current_price = turnip.current_price()
                current_time = turnip.current_time().strftime("%a, %I:%M %p")
                
                desc = ""
                if description is not None and description.strip() != "":
                    desc = f"\n**{ctx.author.name}** adds: {description}"

                async def on_sent(msg, params):
                    await msg.add_reaction('🦝')
                    self.bot.associated_user[msg.id] = ctx.author.id
                    self.bot.associated_message[ctx.author.id] = msg

                to_send += [(self.bot.report_channel, status, locals(), on_sent)]

            elif status == social_manager.Action.CONFIRM_LISTING_UPDATED:
                to_send += [(ctx, status)]
            elif status == social_manager.Action.UPDATE_LISTING:
                owner_id, current_price, desc, time = action[1:]
                if owner_id in self.bot.associated_message:
                    msg = self.bot.associated_message[owner_id]
                    current_time = time.strftime("%a, %I:%M %p")
                    name = ctx.author.name

                    if desc is not None and desc.strip() != "":
                        desc = f"\n**{ctx.author.name}** adds: {desc}"

                    to_send += [(msg, social_manager.Action.POST_LISTING, locals())]
                else:
                    logger.error(f"{ctx.author.name} tried to update a listing that doesn't exist anymore.")

        return to_send
    
    @host.error
    async def host_error(self, ctx, error):
        logger.info(f"Invalid command received: {ctx.message.content}")
        logger.info(error)
        await ctx.send("Usage: \"host [price] [optional dodo code] [optional gmt offset--an integer such as -5 or 8] [optional description, markdown supported]\"\n\n "
                "The quotes (\") and square brackets ([]) are **not** part of the input!\n\n"
                "Example usage: `host 123 C0FEE 8 Brewster is in town selling infinite durability axes`\n\n "
                "All arguments are required if you wish to include a description, but feel free to put a placeholder price like 1 if you are opening for reasons other than turnips.")

class Lloid(commands.Bot):
    Successful = 0
    AlreadyClosed = 1
    QueueEmpty = 2

    def __init__(self):
        super().__init__(command_prefix=self.get_prefix, case_insensitive=True)

        # Automatically discover cogs
        members = inspect.getmembers(sys.modules[__name__], inspect.isclass)
        for _, Member in members:
            if issubclass(Member, commands.Cog):
                self.add_cog(Member(self))

    async def get_prefix(self, message):
        if not message.guild:
            return ['!', '']
        
        # Server-specific prefixes could be implemented here.
        return commands.when_mentioned_or('!')(self, message)

    async def on_command_error(self, ctx, error):
        if (
            isinstance(error, commands.CheckFailure)
            or isinstance(error, commands.CommandNotFound)
            or isinstance(error, commands.DisabledCommand)
        ):
            logger.debug("Invalid command, error:")
            logger.debug(error)
            await ctx.send("** If you've used this bot before, note that the syntax has changed slightly.**")
            await ctx.send("Usage: \"host [price] [optional dodo code] [optional gmt offset--an integer such as -5 or 8] [optional description, markdown supported]\"\n\n "
                    "The quotes (\") and square brackets ([]) are **not** part of the input!\n\n"
                    "Example usage: `host 123 C0FEE 8 Brewster is in town selling infinite durability axes`\n\n "
                    "All arguments are required if you wish to include a description, but feel free to put a placeholder price like 1 if you are opening for reasons other than turnips.")
            return

    async def on_ready(self):
        logger.info('Logged on as {0}!'.format(self.user))
        if self.initialized is None or not self.initialized:
            logger.info("Initializing.")
            self.initialized = True
            self.report_channel = self.get_channel(int(os.getenv("ANNOUNCE_ID")))
            self.chan = 'global'
            self.db = sqlite3.connect("test.db") 
            self.market = turnips.StalkMarket(self.db)
            self.associated_user = {} # message id -> id of the user the message is about
            self.associated_message = {} # reverse mapping of the above
            self.sleepers = {}
            self.recently_departed = {}
            self.requested_pauses = {} # owner -> int representing number of requested pauses remaining 
            self.is_paused = {} # owner -> boolean

            queuer = queue_manager.QueueManager(self.market)
            self.social_manager = social_manager.TimedSocialManager(queuer)

            deleted = await self.report_channel.purge(check=lambda m: m.author==self.user)
            num_del = len(deleted)
            logger.info(f"Initialized. Deleted {num_del} old messages.")
        logger.info(f"Sample data to verify data integrity: {self.associated_user}")

    async def on_raw_reaction_add(self, payload):
        channel = await self.fetch_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        user = await self.fetch_user(payload.user_id)
        host_user = await self.fetch_user(self.associated_user[payload.message_id])

        if user == self.user or message.author != self.user:
            return

        if payload.emoji.name == '🦝':
            logger.debug(f"{user.name} reacted with raccoon")
            try:
                res = self.social_manager.reaction_added(payload.user_id, host_user.id)
                if len(res) > 0 and res[0][0] == social_manager.Action.CONFIRM_QUEUED:
                    guest, host, ahead = res[0][1:]
                    await self.confirm_queued(guest, host, ahead)
            except:
                logger.warning(f"User {user.name} tried to queue up, but isn't allowing DMs.")
                self.market.forfeit(user.id)
                await message.remove_reaction('🦝', user)

    @sends_messages
    async def confirm_queued(self, guest, host, ahead):
        owner_name = self.get_user(host).name
        worst_case = queue_interval_minutes
        best_case = worst_case//2
        interval_s = best_case*len(ahead)
        interval_e = worst_case*(len(ahead)+1)
        queue_interval = queue_interval_minutes

        return [(self.get_user(guest), social_manager.Action.CONFIRM_QUEUED, locals())]

    async def on_raw_reaction_remove(self, payload):
        if payload.emoji.name == '🦝' and payload.message_id in self.associated_user and payload.user_id in self.market.queue.requesters:
            user = await self.fetch_user(payload.user_id)
            logger.debug(f"{user.name} unreacted with raccoon")
            owner_name = self.get_user(self.associated_user[payload.message_id]).name
            waiting_for = self.market.queue.requesters[payload.user_id]
            if waiting_for == self.associated_user[payload.message_id] and self.market.forfeit(payload.user_id):
                await user.send("Removed you from the queue for %s." % owner_name)

    async def on_disconnect(self):
        logger.warning("Lloid got disconnected.")

    async def let_next_person_in(self, owner):
        task = None
        task, status = self.market.next(owner)
        if status == turnips.Status.QUEUE_EMPTY:
            return Lloid.QueueEmpty
        elif status == turnips.Status.ALREADY_CLOSED: # Then the owner closed
            logger.info(f"Closed queue for {owner}")
            return Lloid.AlreadyClosed

        logger.info(f"Letting {self.get_user(task[0]).name} in to {task[1].name}")
        msg = await self.get_user(task[0]).send(f"Hope you enjoy your trip to **{task[1].name}**'s island! "
        "Be polite, observe social distancing, leave a tip if you can, and **please be responsible and message me \"__done__\" when you've left "
        "(unless the island already has a lot of visitors inside, in which case... don't bother)**. Doing this lets the next visitor in."
        f"The Dodo code is **{task[1].dodo}**.")
        if msg is None:
            logger.error("Failed to let them in!")
        else:
            logger.info(f"Sent out a code, message id is {msg.id}")
        q = self.market.queue.queues[owner]
        logger.info(f"Remainder in queue = {len(q)}")
        if len(q) > 0:
            logger.info(f"looking up {q[0][0]}")
            next_in_line = self.get_user(q[0][0])
            if next_in_line is not None:
                logger.info(f"Sending warning to {next_in_line.name}")
                await next_in_line.send(f"Your flight to **{task[1].name}**'s island is boarding soon! "
                f"Please have your tickets ready, we'll be calling you forward some time in the next 0-{queue_interval_minutes} minutes!")
                #if owner in self.descriptions and self.descriptions is not None and self.descriptions[owner].strip() != "":
                #    desc = self.descriptions[owner]
                #    await next_in_line.send(f"By the way, here's the current description of the island, in case you need a review or in case it's been updated since you last viewed the listing:\n\n{desc}")
        logger.info(f"{self.get_user(task[0]).name} has departed for {task[1].name}'s island")
        self.recently_departed[task[0]] = owner
        try:
            await self.associated_message[owner].remove_reaction('🦝', self.get_user(task[0]))
        except Exception as ex:
            logger.warning("Couldn't remove reaction; error: %s" % ex)

        logger.debug("should have been successful")
        return Lloid.Successful

    async def reset_sleep(self, owner):
        logger.info("Resetting sleep")
        if owner in self.sleepers:
            logger.info("Cancelling current sleep")
            self.sleepers[owner].cancel()
        self.sleepers[owner] = self.loop.create_task(asyncio.sleep(queue_interval))

        try:
            await self.sleepers[owner]
            owner_name = self.get_user(owner).name
            logger.info(f"Timeout on last visitor to {owner_name}, letting next person in.")
        except:
            logger.info("Sleep was cancelled")
            pass

        if owner in self.sleepers:  # not yet sure why sometimes owner is not in self.sleepers
            del self.sleepers[owner]

    async def queue_manager(self, owner):
        self.is_paused[owner] = False
        while True:
            # pauses should go here because the queue might be empty when the owner calls pause
            # if it's empty when that happens, then it never reaches the reset_sleep call at the end.
            # we can't move that reset_sleep call up here because that means it would sleep before handing
            # out the first code.
            while owner in self.requested_pauses and self.requested_pauses[owner] > 0:
                logger.info(f"Sleeping upon request, {self.requested_pauses[owner]}")
                self.is_paused[owner] = True
                self.requested_pauses[owner] -= 1
                await self.reset_sleep(owner)
            self.is_paused[owner] = False

            status = await self.let_next_person_in(owner)
            if status == Lloid.QueueEmpty:
                # print("queue seems empty, sleeping then polling again")
                await asyncio.sleep(poll_sleep_interval)
                continue
            elif status == Lloid.AlreadyClosed:
                logger.warning("Lloid apparently closed")
                break

            print("Should reset sleep now")
            await self.reset_sleep(owner)
        logger.warning("Exited the loop. This can only happen if the queue was closed.")

    async def on_message(self, message):
        # Lloid should not respond to self
        if message.author == self.user:
            return

        # This entire handler can be removed, but if it's defined, the line below *must* be executed
        # otherwise commands are not processed at all.
        await self.process_commands(message)
        
def main(): 
    parser = argparse.ArgumentParser()
    parser.add_argument('--verbose', '-v', action='count', help='Sets the verbosity level of the logger.', default=0, required=False)
    args = parser.parse_args()
    verbosity = args.verbose
    log_level = logging.WARNING

    if verbosity >= 2:
        log_level = logging.DEBUG
    elif verbosity >= 1:
        log_level = logging.INFO
    elif verbosity <= 0:
        log_level = logging.WARNING

    logging.basicConfig(format='[%(asctime)s] %(levelname)s %(filename)s@%(lineno)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')  
    logger.setLevel(log_level)
    logger.info(f"Set logging level to {logging.getLevelName(log_level)}")

    logger.info("Starting Lloid...")
    load_dotenv()
    token = os.getenv("TOKEN")
    interval = os.getenv("QUEUE_INTERVAL")
    sentry_dsn = os.getenv("SENTRY_DSN")

    if not token:
        raise Exception('TOKEN env variable is not defined')

    if not os.getenv("ANNOUNCE_ID"):
        raise Exception('ANNOUNCE_ID env variable is not defined')

    if sentry_dsn:
        sentry_sdk.init(sentry_dsn)
        logger.info("Connected to Sentry")

    if interval:
        queue_interval = int(interval)
        logger.info(f"Set interval to {interval}")

    client = Lloid()
    client.initialized = False
    client.run(token)

if __name__ == "__main__":
    main()