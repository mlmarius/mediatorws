#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DB query tools for stationlite web service.

"""

import datetime
import dateutil
import os

from operator import itemgetter


from sqlalchemy import (
    MetaData, Table, Column, Integer, Float, String, Unicode, DateTime, 
    ForeignKey, create_engine, insert, select, update, and_, func)


from eidangservices import settings
from eidangservices.stationlite.engine import db


def find_snclepochs_and_routes_from_query(
    connection, tables, net, sta, loc, cha, st, et, service):
    """
    Return SNCL epochs and routes for given query parameters.
    
    """
    
    tn = tables['network']['networkepoch']
    tne = tables
    
    ts = tables['station']
    tse = tables['stationepoch']
    
    tc = tables['channel']
    tr = tables['routing']
    te = tables['endpoint']
    tsv = tables['service']
    
    # TODO(fab): correct query
    s = select(
        [tn.c.nametn.c.name, ts.c.name, tc.c.locationcode, tc.c.code, 
         tr.c.starttime, tr.c.endtime, te.c.url]).where(
            and_(
                net == tn.c.name,
                sta == ts.c.name,
                
                loc == tc.c.locationcode,
                cha == tc.c.code,
                db.to_db_timestamp(st) == tc.c.starttime,
                db.to_db_timestamp(et) == tc.c.endtime,
                tc.c.network_ref == tn.c.oid,
                tc.c.network_ref == ts.c.oid,
                
                tr.c.channel_ref == tc.c.oid,
                tr.c.endpoint_ref == te.c.oid,
                
                te.c.service_ref == tsv.c.oid,
                service == tsv.c.name
                
        )
    )
    
    rp = connection.execute(s)
    r = rp.fetchall()
    
    routes = []
        
    for row in r:
        routes.append(
            {'net': row[0],
             'sta': row[1],
             'loc': row[2],
             'cha': row[3],
             'st': row[4],
             'et': row[5],
             'url': row[6]})
    
    return routes


def find_networks(connection, tables):
    
    tn = tables['network']
    
    s = select([tn.c.name])
    
    rp = connection.execute(s)
    r = rp.fetchall()
    
    return [x[0] for x in r]

    
