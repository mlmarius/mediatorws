# -*- coding: utf-8 -*-
"""

EIDA Mediator

This file is part of the EIDA mediator/federator webservices.

"""

import os
import tempfile

import requests

from mediator import settings
from mediator.server import httperrors, parameters


def write_response_string_to_file(outfile, data_str):
    """Write response data to file."""
    
    if not data_str:
        return False
    
    else:
        with open(outfile, 'wb') as fh:
            fh.write(data_str)
            
        return True


def query_federator_for_target_service(
    outfile, service, query_par, snclepochs):
    """
    Query federator service for target service and write response to
    outfile.
    
    """
    
    # create POST data for federator service
    postdata = parameters.get_non_sncl_postlines(query_par)
    postdata += snclepochs.tofdsnpost()
    
    if not postdata:
        raise httperrors.NoDataError()
    
    federator_url = get_federator_query_endpoint(map_service(service))
    
    print federator_url
    print postdata
    
    with open(outfile, 'wb') as fh:
        
        print "issueing POST to federator"
        response = requests.post(federator_url, data=postdata, stream=True)

        if not response.ok:
            error_msg = "federator POST failed with code %s" % (
                response.status_code)
            raise RuntimeError, error_msg

        for block in response.iter_content(1024):
            fh.write(block)
            
    return True


def get_federator_endpoint(fdsn_service='dataselect'):
    """
    Get URL of EIDA federator endpoint depending on requested FDSN service
    (dataselect or station).
    
    """
    
    if not fdsn_service in settings.EIDA_FEDERATOR_SERVICES:
        raise NotImplementedError, "service %s not implemented" % fdsn_service
        
    
    return "%s:%s/fdsnws/%s/1/" % (
        settings.EIDA_FEDERATOR_BASE_URL, settings.EIDA_FEDERATOR_PORT, 
        fdsn_service)


def get_federator_query_endpoint(fdsn_service='dataselect'):
    """
    Get URL of EIDA federator query endpoint depending on requested FDSN 
    service (dataselect or station).
    
    """
    
    endpoint = get_federator_endpoint(fdsn_service)
    return "%s%s" % (endpoint, settings.FDSN_QUERY_METHOD_TOKEN)


def get_event_query_endpoint(event_service):
    """
    Get URL of EIDA federator query endpoint depending on requested FDSN 
    service (dataselect or station).
    
    """
    
    endpoint = get_event_url(event_service)
    return "%s%s" % (endpoint, settings.FDSN_QUERY_METHOD_TOKEN)


def get_routing_url(routing_service):
    """Get routing URL for routing service abbreviation."""
    
    try:
        server = settings.EIDA_NODES[routing_service]['services']['eida']\
            ['routing']['server']
    except KeyError:
        server = settings.EIDA_NODES[settings.DEFAULT_ROUTING_SERVICE]\
            ['services']['eida']['routing']['server']
        
    return "%s%s" % (server, settings.EIDA_ROUTING_PATH)


def get_event_url(event_service):
    """Get event URL for event service abbreviation."""
    
    try:
        server = settings.FDSN_EVENT_SERVICES[event_service]['server']
    except KeyError:
        server = settings.FDSN_EVENT_SERVICES[settings.DEFAULT_EVENT_SERVICE]\
            ['server']
        
    return "%s%s" % (server, settings.FDSN_EVENT_PATH)


def map_service(service):
    """
    Map service parameter of mediator to service path of FDSN/EIDA web service.
    
    """
    
    return parameters.MEDIATOR_SERVICE_PARAMS[service]['map']


def get_temp_filepath():
    """Return path of temporary file."""
    
    return os.path.join(
        tempfile.gettempdir(), next(tempfile._get_candidate_names()))

