
from dbus.service import signal
from dbus.gobject_service import ExportedGObject
import logging
import telepathy
from sugar import profile
from sugar.presence import presenceservice
from sugar.presence.tubeconn import TubeConnection

SERVICE = 'org.laptop.TurtleArtActivity'
IFACE = SERVICE
PATH = '/org/laptop/TurtleArtActivity'
_logger = logging.getLogger('turtleart-activity')

class Collaboration():
    def __init__(self, tw, activity):
        """ A simplistic sharing model: the sharer is the master """
        self._tw = tw
        self._activity = activity

    def setup(self):
        # TODO: hand off role of master is sharer leaves
        self.pservice = presenceservice.get_instance()
        self.initiating = None # sharing (True) or joining (False)

        # Add my buddy object to the list
        owner = self.pservice.get_owner()
        self.owner = owner
        self.tw.buddies.append(self.owner)
        self._share = ""

        self._activity.connect('shared', self._shared_cb)
        self._activity.connect('joined', self._joined_cb)

    def _shared_cb(self, activity):
        self._shared_activity = self._activity._shared_activity
        if self._shared_activity is None:
            _logger.error("Failed to share or join activity ... \
                _shared_activity is null in _shared_cb()")
            return

        self.initiating = True
        self.waiting_for_turtles = False
        self.turtle_dictionary = self._get_dictionary()

        _logger.debug('I am sharing...')

        self.conn = self._shared_activity.telepathy_conn
        self.tubes_chan = self._shared_activity.telepathy_tubes_chan
        self.text_chan = self._shared_activity.telepathy_text_chan

        self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].connect_to_signal(
            'NewTube', self._new_tube_cb)

        _logger.debug('This is my activity: making a tube...')

        id = self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].OfferDBusTube(
            SERVICE, {})

    def _joined_cb(self, activity):
        self._shared_activity = self._activity._shared_activity
        if self._shared_activity is None:
            _logger.error("Failed to share or join activity ... \
                _shared_activity is null in _shared_cb()")
            return

        self.initiating = False
        self.conn = self._shared_activity.telepathy_conn
        self.tubes_chan = self._shared_activity.telepathy_tubes_chan
        self.text_chan = self._shared_activity.telepathy_text_chan

        # call back for "NewTube" signal
        self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].connect_to_signal(
            'NewTube', self._new_tube_cb)

        _logger.debug('I am joining an activity: waiting for a tube...')
        self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].ListTubes(
            reply_handler=self._list_tubes_reply_cb,
            error_handler=self._list_tubes_error_cb)

        # Joiner should request current state from sharer.
        self.waiting_for_turtles = True

    def _list_tubes_reply_cb(self, tubes):
        for tube_info in tubes:
            self._new_tube_cb(*tube_info)

    def _list_tubes_error_cb(self, e):
        _logger.error('ListTubes() failed: %s', e)

    def _new_tube_cb(self, id, initiator, type, service, params, state):
        """ Create a new tube. """
        _logger.debug('New tube: ID=%d initator=%d type=%d service=%s '
                     'params=%r state=%d', id, initiator, type, service,
                     params, state)

        if (type == telepathy.TUBE_TYPE_DBUS and service == SERVICE):
            if state == telepathy.TUBE_STATE_LOCAL_PENDING:
                self.tubes_chan[ \
                              telepathy.CHANNEL_TYPE_TUBES].AcceptDBusTube(id)

            tube_conn = TubeConnection(self.conn,
                self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES], id, \
                group_iface=self.text_chan[telepathy.CHANNEL_INTERFACE_GROUP])

            # We'll use a chat tube to send serialized stacks back and forth.
            self.chattube = ChatTube(tube_conn, self.initiating, \
                self.event_received_cb)

            # Now that we have the tube, we can ask for the turtle dictionary.
            if self.waiting_for_turtles:
                _logger.debug("Sending a request for the turtle dictionary")
                # we need to send our own nick and colors
                colors = self._get_colors()
                event = "t|" + data_to_string([self._get_nick(), colors])
                _logger.debug(event)
                self.send_event(event)

    def event_received_cb(self, text):
        """
        Events are sent as a tuple, nick|cmd, where nick is a turle name
        and cmd is a turtle event. Everyone gets the turtle dictionary from
        the sharer and watches for 't' events, which indicate that a new
        turtle has joined.
        """
        if len(text) == 0:
            return
        # Save active Turtle
        save_active_turtle = self.tw.active_turtle
        e = text.split("|", 2)
        text = e[1]
        if e[0] == 't': # request for turtle dictionary
            if text > 0:
                [nick, colors] = data_from_string(text)
                if nick != self.tw.nick:
                    # There may not be a turtle dictionary.
                    if hasattr(self, "turtle_dictionary"):
                        self.turtle_dictionary[nick] = colors
                    else:
                        self.turtle_dictionary = {nick: colors}
                    # Add new turtle for the joiner.
                    self.tw.canvas.set_turtle(nick, colors)
            # Sharer should send turtle dictionary.
            if self.initiating:
                text = data_to_string(self.turtle_dictionary)
                self.send_event("T|" + text)
        elif e[0] == 'T': # Receiving the turtle dictionary.
            if self.waiting_for_turtles:
                if len(text) > 0:
                    self.turtle_dictionary = data_from_string(text)
                    for nick in self.turtle_dictionary:
                        if nick != self.tw.nick:
                            colors = self.turtle_dictionary[nick]
                            # add new turtle for the joiner
                            self.tw.canvas.set_turtle(nick, colors)
                self.waiting_for_turtles = False
        elif e[0] == 'f': # move a turtle forward
            if len(text) > 0:
                [nick, x] = data_from_string(text)
                if nick != self.tw.nick:
                    self.tw.canvas.set_turtle(nick)
                    self.tw.canvas.forward(x, False)
        elif e[0] == 'a': # move a turtle in an arc
            if len(text) > 0:
                [nick, [a, r]] = data_from_string(text)
                if nick != self.tw.nick:
                    self.tw.canvas.set_turtle(nick)
                    self.tw.canvas.arc(a, r, False)
        elif e[0] == 'r': # rotate turtle
            if len(text) > 0:
                [nick, h] = data_from_string(text)
                if nick != self.tw.nick:
                    self.tw.canvas.set_turtle(nick)
                    self.tw.canvas.seth(h, False)
        elif e[0] == 'x': # set turtle xy position
            if len(text) > 0:
                [nick, [x, y]] = data_from_string(text)
                if nick != self.tw.nick:
                    self.tw.canvas.set_turtle(nick)
                    self.tw.canvas.setxy(x, y, False)
        elif e[0] == 'c': # set turtle pen color
            if len(text) > 0:
                [nick, x] = data_from_string(text)
                if nick != self.tw.nick:
                    self.tw.canvas.set_turtle(nick)
                    self.tw.canvas.setcolor(x, False)
        elif e[0] == 'g': # set turtle pen gray level
            if len(text) > 0:
                [nick, x] = data_from_string(text)
                if nick != self.tw.nick:
                    self.tw.canvas.set_turtle(nick)
                    self.tw.canvas.setgray(x, False)
        elif e[0] == 's': # set turtle pen shade
            if len(text) > 0:
                [nick, x] = data_from_string(text)
                if nick != self.tw.nick:
                    self.tw.canvas.set_turtle(nick)
                    self.tw.canvas.setshade(x, False)
        elif e[0] == 'w': # set turtle pen width
            if len(text) > 0:
                [nick, x] = data_from_string(text)
                if nick != self.tw.nick:
                    self.tw.canvas.set_turtle(nick)
                    self.tw.canvas.setpensize(x, False)
        elif e[0] == 'p': # set turtle pen state
            if len(text) > 0:
                [nick, x] = data_from_string(text)
                if nick != self.tw.nick:
                    self.tw.canvas.set_turtle(nick)
                    self.tw.canvas.setpen(x, False)
        # Restore active Turtle
        self.tw.canvas.set_turtle(self.tw.turtles.get_turtle_key(
                save_active_turtle))

    def send_event(self, entry):
        """ Send event through the tube. """
        if hasattr(self, 'chattube') and self.chattube is not None:
            self.chattube.SendText(entry)

    def _get_dictionary(self):
        d = { self._get_nick(): self._get_colors()}
        return d

    def _get_nick(self):
        return self.tw.nick

    def _get_colors(self):
        return profile.get_color().to_string()

class ChatTube(ExportedGObject):

    def __init__(self, tube, is_initiator, stack_received_cb):
        """Class for setting up tube for sharing."""
        super(ChatTube, self).__init__(tube, PATH)
        self.tube = tube
        self.is_initiator = is_initiator # Are we sharing or joining activity?
        self.stack_received_cb = stack_received_cb
        self.stack = ''

        self.tube.add_signal_receiver(self.send_stack_cb, 'SendText', IFACE, \
            path=PATH, sender_keyword='sender')

    def send_stack_cb(self, text, sender=None):
        if sender == self.tube.get_unique_name():
            return
        self.stack = text
        self.stack_received_cb(text)

    @signal(dbus_interface=IFACE, signature='s')
    def SendText(self, text):
        self.stack = text