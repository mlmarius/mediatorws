# -*- coding: utf-8 -*-
#
# -----------------------------------------------------------------------------
# This file is part of EIDA NG webservices (eida-federator).
#
# eida-federator is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# eida-federator is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
# ----
#
# Copyright (c) Daniel Armbruster (ETH), Fabian Euchner (ETH)
#
# -----------------------------------------------------------------------------
"""
This file is part of the EIDA mediator/federator webservices.

"""

import logging

from flask import request, send_file
from webargs.flaskparser import use_args

import eidangservices as eidangws

from eidangservices import settings, utils
from eidangservices.federator.server import general_request, schema
from eidangservices.federator.server.route import PlainDownloadTask
from eidangservices.federator.server.combine import VPVSCombiner

from multiprocessing.pool import ThreadPool
import tempfile
import os
import urllib
import datetime

class VPVSResource(general_request.GeneralResource):

    LOGGER = 'flask.app.federator.vpvs_resource'

    def __init__(self):
        super(VPVSResource, self).__init__()
        self.logger = logging.getLogger(self.LOGGER)

    @use_args(schema.VPVSSchema(), locations=('query',))
    def get(self, args):
        # request.method == 'GET'

        # serialize objects
	
        # self.logger.debug('VPVSSchema (serialized): %s' % args.data)

        
        bytes_fetched = []
       
        nodes = [
                    'http://vpvs.infp.ro/query',  # VRANCEA NFO
                    'http://webservices.ingv.it/ingvws/nfo_taboo/vpvs/1/query' # INGV NFO
                ]

        for key in ['maxtime', 'mintime']:
            if args.data.get(key) and isinstance(args.data[key], datetime.datetime):
                args.data[key] = args.data[key].isoformat()+'.00'

        combiner = VPVSCombiner()

        def start_thread():
            logging.info('Now starting to download')
            return

        thread_pool = ThreadPool(processes=4, initializer=start_thread)

        for node in nodes:
            url = urllib.urlencode(args.data)
            self.logger.debug('*** adding url %s' % url)
            
            thread_pool.apply_async(
                PlainDownloadTask("%s?%s" % (node, url), combiner=combiner)) 
            
        thread_pool.close()
        thread_pool.join()
    
        tmp = tempfile.mkstemp()[1]
        try:
            with open(tmp, 'wb') as fd:
                combiner.dump(fd)
        except Exception:
	    self.logger.error('Could not prepare result', exc_info=True)
            os.remove(tmp)
            return "Could not prepare result"
        
        try:
            return send_file(tmp, mimetype='application/json')
        finally:
            os.remove(tmp)


    def _get_result_mimetype(self, args):
        """Return result mimetype (either XML or plain text."""
        try:
            args['format'] == 'text'
            return settings.STATION_MIMETYPE_TEXT
        except KeyError:
            return settings.STATION_MIMETYPE_XML

    # _get_result_mimetype ()

#
