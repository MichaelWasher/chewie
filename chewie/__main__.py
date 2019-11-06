import logging
import sys
import argparse
from os import path

from chewie.chewie import Chewie
from chewie.config_parser import parse_chewie_config

def get_logger(name, log_level=logging.DEBUG):
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(log_level)
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(log_level)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        ch.setFormatter(formatter)
        logger.addHandler(ch)
    return logger


def auth_handler(address, group_address, *args, **kwargs):
    #TODO Make external calls to Faucet
    logger = get_logger("CHEWIE")
    logger.info("Authentication successful for address {} on port {}".format(str(address), str(group_address)))
    logger.info("Arguments passed from Chewie to Faucet: \n*args:{}\n**kwargs{}".format(str(
        args), str(kwargs)))


def failure_handler(address, group_address):
    #TODO Make external calls to Faucet
    print("failure of address %s on port %s" % (str(address), str(group_address)))


def logoff_handler(address, group_address):
    #TODO Make external calls to Faucet
    print("logoff of address %s on port %s" % (str(address), str(group_address)))


# Load Config from Config File
def check_args(args):
    """Check that all arguments are given as expected"""
    #TODO implement
    pass


def get_args():
    """Get Input Arguments"""
    parser = argparse.ArgumentParser(description='Run Chewie 802.1x Authenticator independently of '
                                                 'Faucet SDN Controller')

    parser.add_argument('-i', '--interface', dest='interface',
                        help='Set the interface for Chewie to listen on - Default: eth0',
                        default="eth0")
    parser.add_argument('-ri', '--radius_ip', dest='radius_ip',
                        help='Set the IP Address for the RADIUS Server that Chewie will forward requests to '
                             '- DEFAULT: 127.0.0.1', default='127.0.0.1')
    parser.add_argument('-rs', '--radius_secret', dest='radius_secret',
                        help='Set the Secret used for connecting to the RADIUS Server - Default: SECRET',
                        default='SECRET')
    parser.add_argument('-c', '--configuration_file', dest='configuration_file',
                        help='Set the YAML configuration file for Chewie. Default: chewie.yaml',
                        default='chewie.yaml')

    return parser.parse_args()


def check_configuration_file_present(args):
    """Check the configuration file exists"""
    filename = args.configuration_file
    return path.exists(filename) and path.isfile(filename)

LOGNAME="CHEWIE"
def main():
    logger = get_logger(LOGNAME)
    logger.info('Starting Chewie...')

    args = get_args()
    check_args(args)

    if check_configuration_file_present(args):
        config_file = args.configuration_file
        config = parse_chewie_config(config_file, LOGNAME)

        chewie = Chewie(config['dp_interface'], logger, auth_handler, failure_handler, logoff_handler,
                        radius_server_ip=config['radius_ip'], radius_server_secret=config['radius_secret'])
    else:
        chewie = Chewie(args.interface, logger, auth_handler, failure_handler, logoff_handler,
                        radius_server_ip=args.radius_ip, radius_server_secret=args.radius_secret)
    chewie.run()


if __name__ == '__main__':
    main()
