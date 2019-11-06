"""Very Simple Configuration for Chewie"""

#
# Example Configuration:

# dp_interface
# radius_interface
# radius_ip
# radius_port
# radius_secret
# Enable MAB
# Enable EAP
# Enable preemptive EAP Requests

from chewie import config_parser_util

class ConfigurationException(Exception):
    pass

def parse_chewie_config(config_file, logname):
    """"""
    conf = config_parser_util.read_config(config_file, logname)

    # Enforce Requirements
    check_conf_overview(conf)
    check_conf_radius_ip(conf)
    check_conf_radius_interface(conf)
    check_conf_dp_interface(conf)

    return conf

def check_conf_radius_ip(conf):
    #TODO
    pass

def check_conf_radius_interface(conf):
    #TODO
    pass


def check_conf_dp_interface(conf):
    #TODO
    pass


def check_conf_overview(conf_data):
    # Basic expected keys
    if 'radius_ip' not in conf_data:
        raise ConfigurationException('An IP Address for the RADIUS server must be provided.')
    if 'radius_secret' not in conf_data:
        raise ConfigurationException('A Secret Key for the RADIUS server must be provided.')
    if 'dp_interface' not in conf_data:
        raise ConfigurationException('The NFV interface name must be provided.')
    if 'radius_interface' not in conf_data:
        raise ConfigurationException('The RADIUS-facing interface name must be provided.')
    return
