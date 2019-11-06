""""""

import logging
import time
import faucet_event_client

LOGGER = logging.getLogger('runner')

class FaucetEventController:
    """"""
    # TODO -  remove chewie dependency and replace with an observer pattern
    def __init__(self, chewie):
        self.faucet_events = None
        self.chewie = chewie

    def _flush_faucet_events(self):
        LOGGER.info('Flushing faucet event queue...')
        if self.faucet_events:
            while self.faucet_events.next_event():
                pass

    def initialize(self):
        """Initialize DAQ instance"""
        LOGGER.debug('Attaching event channel...')
        self.faucet_events = faucet_event_client.FaucetEventClient(self.config)
        self.faucet_events.connect()

        LOGGER.info('Waiting for system to settle...')
        time.sleep(3)

        LOGGER.debug('Done with initialization')

    def _handle_faucet_events(self):
        while self.faucet_events:
            event = self.faucet_events.next_event()
            LOGGER.debug('Faucet event %s', event)
            if not event:
                break
            (dpid, port, active) = self.faucet_events.as_port_state(event)
            if dpid and port:
                self._handle_port_state(dpid, port, active)
            else:
                LOGGER.debug('Other Event Occurred: %s', str(event.__dict__))

    def _port_up(self, port_id):
        self.chewie.port_up(port_id)

    def _handle_port_state(self, dpid, port, active):
        LOGGER.debug('Port status event occured')

        #TODO implement check for port up and down
        self.port_up(port)

    def shutdown(self):
        """Shutdown this runner by closing all active components"""
        self.faucet_events.disconnect()
        self.faucet_events = None