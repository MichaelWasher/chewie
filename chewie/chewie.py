"""Entry point for 802.1X speaker.
"""
import os
import struct

from chewie import timer_scheduler
from eventlet import sleep, GreenPool
from eventlet.green import socket
from eventlet.queue import Queue

from chewie.eap_socket import EapSocket
from chewie.radius_socket import RadiusSocket
from chewie.eap_state_machine import FullEAPStateMachine
from chewie.radius_attributes import EAPMessage, State, CalledStationId, NASIdentifier, NASPortType
from chewie.message_parser import MessageParser, MessagePacker
from chewie.mac_address import MacAddress
from chewie.event import EventMessageReceived, EventRadiusMessageReceived, EventPortStatusChange
from chewie.utils import get_logger
from chewie.radius import Radius

def unpack_byte_string(byte_string):
    """unpacks a byte string"""
    return "".join("%02x" % x for x in byte_string)


def port_id_to_int(port_id):
    """"Convert a port_id str '00:00:00:00:aa:01 to integer'"""
    dp, port = str(port_id).split(':')[4:]
    return int.from_bytes(struct.pack('!HH', int(dp, 16), int(port, 16)), 'big')  # pytype: disable=attribute-error


class Chewie:
    """Facilitates EAP supplicant and RADIUS server communication"""
    RADIUS_AUTH_UDP_PORT = 1812
    RADIUS_ACCT_UDP_PORT = 1813

    def __init__(self, interface_name, logger=None,
                 auth_handler=None, failure_handler=None, logoff_handler=None,
                 radius_server_ip=None, radius_auth_server_port=None, radius_acct_server_port=None,
                 radius_server_secret=None, chewie_id=None):
        self.interface_name = interface_name
        self.logger = get_logger(logger.name + "." + Chewie.__name__)
        self.auth_handler = auth_handler
        self.failure_handler = failure_handler
        self.logoff_handler = logoff_handler

        self.radius_server_ip = radius_server_ip
        self.radius_secret = radius_server_secret
        self.radius_auth_server_port = self.RADIUS_AUTH_UDP_PORT
        self.radius_acct_server_port = self.RADIUS_ACCT_UDP_PORT
        if radius_auth_server_port:
            self.radius_auth_server_port = radius_auth_server_port
        if radius_acct_server_port:
            self.radius_acct_server_port = radius_acct_server_port

        self.radius_listen_ip = "0.0.0.0"
        self.radius_listen_port = 0

        self.chewie_id = "44-44-44-44-44-44:"  # used by the RADIUS Attribute
                                               # 'Called-Station' in Access-Request
        if chewie_id:
            self.chewie_id = chewie_id
        self.extra_radius_request_attributes = self.prepare_extra_radius_attributes()

        self.state_machines = {}  # mac: state_machine
        self.packet_id_to_mac = {}  # radius_packet_id: mac
        self.packet_id_to_request_authenticator = {}

        self.eap_output_messages = Queue()
        self.radius_output_messages = Queue()

        self.timer_scheduler = timer_scheduler.TimerScheduler(self.logger)

        self.radius_id = -1
        self.eap_socket = None
        self.pool = None
        self.eventlets = None
        self.radius_socket = None
        self.interface_index = None

        self.eventlets = []

    def run(self):
        """setup chewie and start socket eventlet threads"""
        self.logger.info("Starting")
        self.setup_eap_socket()
        self.setup_radius_socket()
        self.start_threads_and_wait()

    def shutdown(self):
        """kill eventlets and quit"""
        for eventlet in self.eventlets:
            eventlet.kill()

    def start_threads_and_wait(self):
        """Start the thread and wait until they complete (hopefully never)"""
        self.pool = GreenPool()

        self.eventlets.append(self.pool.spawn(self.send_eap_messages))
        self.eventlets.append(self.pool.spawn(self.receive_eap_messages))

        self.eventlets.append(self.pool.spawn(self.send_radius_messages))
        self.eventlets.append(self.pool.spawn(self.receive_radius_messages))

        self.eventlets.append(self.pool.spawn(self.timer_scheduler.run))

        self.pool.waitall()

    def auth_success(self, src_mac, port_id):
        """authentication shim between faucet and chewie
        Args:
            src_mac (MacAddress): the mac of the successful supplicant
            port_id (MacAddress): the 'mac' identifier of what switch port the success is on"""
        if self.auth_handler:
            self.auth_handler(src_mac, port_id)

    def auth_failure(self, src_mac, port_id):
        """failure shim between faucet and chewie
        Args:
            src_mac (MacAddress): the mac of the failed supplicant
            port_id (MacAddress): the 'mac' identifier of what switch port
             the failure is on"""
        if self.failure_handler:
            self.failure_handler(src_mac, port_id)

    def auth_logoff(self, src_mac, port_id):
        """logoff shim between faucet and chewie
        Args:
            src_mac (MacAddress): the mac of the logoff supplicant
            port_id (MacAddress): the 'mac' identifier of what switch port
             the logoff is on"""
        if self.logoff_handler:
            self.logoff_handler(src_mac, port_id)

    def port_down(self, port_id):
        """
        should be called by faucet when port has gone down.
        Args:
            port_id (str): id of port.
        """
        # all chewie needs to do is change its internal state.
        # faucet will remove the acls by itself.
        self.set_port_status(port_id, False)

    def port_up(self, port_id):
        """
        should be called by faucet when port has come up
        Args:
            port_id (str): id of port.
        """
        self.set_port_status(port_id, True)
        # TODO send preemptive identity request.

    def set_port_status(self, port_id, status):
        port_id_str = str(port_id)
        if port_id_str not in self.state_machines:
            self.state_machines[port_id_str] = {}

        for src_mac, state_machine in self.state_machines[port_id_str].items():
            event = EventPortStatusChange(status)
            state_machine.event(event)

    def setup_eap_socket(self):
        self.eap_socket = EapSocket(self.interface_name)
        self.eap_socket.setup()

    def setup_radius_socket(self):
        self.radius_socket = RadiusSocket(self.radius_listen_ip,
                                          self.radius_listen_port,
                                          self.radius_server_ip,
                                          self.radius_auth_server_port,
                                          self.radius_acct_server_port)
        self.radius_socket.setup()
        self.logger.info("Radius Listening on %s:%d" % (self.radius_listen_ip,
                                                        self.radius_listen_port))

    def send_eap_messages(self):
        """send eap messages to supplicant forever."""
        while True:
            try:
                sleep(0)
                message, src_mac, port_mac = self.eap_output_messages.get()
                self.logger.info("Sending message %s from %s to %s" %
                                 (message, str(port_mac), str(src_mac)))
                self.eap_socket.send(MessagePacker.ethernet_pack(message, port_mac, src_mac))
            except Exception as e:
                self.logger.exception(e)

    def receive_eap_messages(self):
        """receive eap messages from supplicant forever."""
        while True:
            try:
                sleep(0)
                self.logger.info("waiting for eap.")
                packed_message = self.eap_socket.receive()
                self.logger.info("Received packed_message: %s", str(packed_message))

                message, dst_mac = MessageParser.ethernet_parse(packed_message)
                self.logger.info("eap EAP(): %s", message)
                self.logger.info("Received message: %s" % message.__dict__)
                state_machine = self.get_state_machine(message.src_mac, dst_mac)
                event = EventMessageReceived(message, dst_mac)
                state_machine.event(event)
            except Exception as e:
                self.logger.exception(e)

    def send_radius_messages(self):
        """send RADIUS messages to RADIUS Server forever."""
        while True:
            try:
                sleep(0)
                eap_message, src_mac, username, state, port_id = self.radius_output_messages.get()
                self.logger.info("got eap to send to radius.. mac: %s %s, username: %s",
                                 type(src_mac), src_mac, username)
                state_dict = None
                if state:
                    state_dict = state.__dict__
                self.logger.info("Sending to RADIUS eap message %s with state %s",
                                 eap_message.__dict__, state_dict)
                radius_packet_id = self.get_next_radius_packet_id()
                self.packet_id_to_mac[radius_packet_id] = {'src_mac': src_mac, 'port_id': port_id}
                # message is eap. needs to be wrapped into a radius packet.
                request_authenticator = os.urandom(16)
                self.packet_id_to_request_authenticator[radius_packet_id] = request_authenticator
                data = MessagePacker.radius_pack(eap_message, src_mac, username,
                                                 radius_packet_id, request_authenticator, state,
                                                 self.radius_secret,
                                                 port_id_to_int(port_id),
                                                 self.extra_radius_request_attributes)

                # Need to know here if it's going to accounting
                # TODO Replace this with inheritance in next iteration
                if state and state.DATA_TYPE.DATA_TYPE_VALUE is Radius.ACCOUNTING_REQUEST:
                    self.radius_socket.send_acct(data)
                else:
                    self.radius_socket.send(data)

                self.logger.info("sent radius message.")
            except Exception as e:
                self.logger.exception(e)

    def receive_radius_messages(self):
        """receive RADIUS messages from RADIUS server forever."""
        while True:
            try:
                sleep(0)
                self.logger.info("waiting for radius.")
                packed_message = self.radius_socket.receive()
                radius = MessageParser.radius_parse(packed_message, self.radius_secret,
                                                    self.request_authenticator_callback)
                self.logger.info("Received RADIUS message: %s", radius)
                state_machine = self.get_state_machine_from_radius_packet_id(radius.packet_id)
                state = radius.attributes.find(State.DESCRIPTION)
                eap_msg_attribute = radius.attributes.find(EAPMessage.DESCRIPTION)

                # Cannot assume that this contains an EAP (Accounting)
                if eap_msg_attribute:
                    eap_msg = eap_msg_attribute.data_type.data()

                self.logger.info("radius EAP: %s", eap_msg)
                event = EventRadiusMessageReceived(eap_msg, state, radius.attributes.to_dict())
                state_machine.event(event)
            except Exception as e:
                self.logger.exception(e)

    def request_authenticator_callback(self, packet_id):
        """Callback to get the RADIUS request Authenticator
        Args:
            packet_id (int):
        Returns:
            the request authenticator sent with the packet_id
            """
        return self.packet_id_to_request_authenticator[packet_id]

    def prepare_extra_radius_attributes(self):
        """Create RADIUS Attirbutes to be sent with every RADIUS request"""
        attr_list = [CalledStationId.create(self.chewie_id),
                     NASPortType.create(15),
                     NASIdentifier.create(self.chewie_id)]
        return attr_list

    def get_state_machine_from_radius_packet_id(self, packet_id):
        """Gets a FullEAPStateMachine from the RADIUS message packet_id
        Args:
            packet_id (int): id of the received RADIUS message
        Returns:
            FullEAPStateMachine
        """
        return self.get_state_machine(**self.packet_id_to_mac[packet_id])

    def get_state_machine(self, src_mac, port_id):
        """Gets or creates if it does not already exist an FullEAPStateMachine for the src_mac.
        Args:
            src_mac (MacAddress): who's to get.
            port_id (MacAddress): ID of the port where the src_mac is.

        Returns:
            FullEAPStateMachine
        """
        port_id_str = str(port_id)
        src_mac_str = str(src_mac)
        port_state_machines = self.state_machines.get(port_id_str, None)
        if port_state_machines is None:
            self.state_machines[port_id_str] = {}
        state_machine = self.state_machines[port_id_str].get(src_mac_str, None)
        if not state_machine:
            state_machine = FullEAPStateMachine(self.eap_output_messages, self.radius_output_messages, src_mac,
                                                self.timer_scheduler, self.auth_success,
                                                self.auth_failure, self.auth_logoff, self.logger.name)
            state_machine.eapRestart = True
            # TODO what if port is not actually enabled, but then how did they auth?
            state_machine.portEnabled = True
            self.state_machines[port_id_str][src_mac_str] = state_machine
        return state_machine

    def get_next_radius_packet_id(self):
        """Calulate the next RADIUS Packet ID
        Returns:
            int
        """
        self.radius_id += 1
        if self.radius_id > 255:
            self.radius_id = 0
        return self.radius_id
