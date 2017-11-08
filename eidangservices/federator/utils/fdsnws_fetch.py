#!/usr/bin/env python
# -*- coding: utf-8 -*-

###############################################################################
# (C) 2014-2016 Helmholtz-Zentrum Potsdam - Deutsches GeoForschungsZentrum GFZ#
#                                                                             #
# License: LGPLv3 (https://www.gnu.org/copyleft/lesser.html)                  #
#                                                                             #
# modified by Fabian Euchner, ETHZ                                            #
#                                                                             #
# original file from (2016-10-26)                                             #
# https://github.com/SeisComP3/seiscomp3/blob/master/src/trunk/apps/fdsnws/\  #
#    fdsnws_fetch.py                                                          #
###############################################################################

"""
A command-line FDSN Web Service client using EIDA routing and authentication.

Usage Examples
==============

Request 60 minutes of the ``"LHZ"`` channel of EIDA stations starting with
``"A"`` for a seismic event around 2010-02-27 07:00 (UTC). Optionally add
``"-v"`` for verbosity. Resulting Mini-SEED data will be written to file
``"data.mseed"``.

.. code-block:: bash

    $ %(prog)s -N '*' -S 'A*' -L '*' -C 'LHZ' \
-s "2010-02-27T07:00:00Z" -e "2010-02-27T08:00:00Z" -v -o data.mseed

The above request is anonymous and therefore restricted data will not be
included. To include restricted data, use a file containing a token obtained
from an EIDA authentication service and/or a CSV file with username and
password for each node not implementing the EIDA auth extension.

.. code-block:: bash

    $ %(prog)s -a token.asc -c credentials.csv -N '*' -S 'A*' -L '*' -C 'LHZ' \
-s "2010-02-27T07:00:00Z" -e "2010-02-27T08:00:00Z" -v -o data.mseed

StationXML metadata for the above request can be requested using the following
command:

.. code-block:: bash

    $ %(prog)s -N '*' -S 'A*' -L '*' -C 'LHZ' \
-s "2010-02-27T07:00:00Z" -e "2010-02-27T08:00:00Z" -y station \
-q level=response -v -o station.xml

Multiple query parameters can be used:

.. code-block:: bash

    $ %(prog)s -N '*' -S '*' -L '*' -C '*' \
-s "2010-02-27T07:00:00Z" -e "2010-02-27T08:00:00Z" -y station \
-q format=text -q level=channel -q latitude=20 -q longitude=-150 \
-q maxradius=15 -v -o station.txt

Bulk requests can be made in ArcLink (-f), breq_fast (-b) or native FDSNWS POST
(-p) format. Query parameters should not be included in the request file, but
specified on the command line.

.. code-block:: bash

    $ %(prog)s -p request.txt -y station -q level=channel -v -o station.xml
"""
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import sys
import time
import datetime
import optparse
import threading
import socket
import csv
import re
import struct

try:
    # Python 3.2 and earlier
    from xml.etree import cElementTree as ET  # NOQA

except ImportError:
    from xml.etree import ElementTree as ET  # NOQA

try:
    # Python 2.x
    import Queue
    import urllib2
    import urlparse
    import urllib

except ImportError:
    # Python 3.x
    import queue as Queue
    import urllib.request as urllib2
    import urllib.parse as urlparse
    import urllib.parse as urllib

VERSION = "2016.140"

GET_PARAMS = set(('net', 'network',
                  'sta', 'station',
                  'loc', 'location',
                  'cha', 'channel',
                  'start', 'starttime',
                  'end', 'endtime',
                  'service',
                  'alternative'))

POST_PARAMS = set(('service',
                   'alternative'))

STATIONXML_RESOURCE_METADATA_ELEMENTS = (
    '{http://www.fdsn.org/xml/station/1}Source', 
    '{http://www.fdsn.org/xml/station/1}Created',
    '{http://www.fdsn.org/xml/station/1}Sender', 
    '{http://www.fdsn.org/xml/station/1}Module', 
    '{http://www.fdsn.org/xml/station/1}ModuleURI')

STATIONXML_NETWORK_ELEMENT = '{http://www.fdsn.org/xml/station/1}Network'
STATIONXML_STATION_ELEMENT = '{http://www.fdsn.org/xml/station/1}Station'
STATIONXML_LATITUDE_ELEMENT = '{http://www.fdsn.org/xml/station/1}Latitude'
STATIONXML_LONGITUDE_ELEMENT = '{http://www.fdsn.org/xml/station/1}Longitude'

STATION_RESPONSE_TEXT_HEADER = \
    '#Network|Station|Latitude|Longitude|Elevation|SiteName|StartTime|EndTime'

FDSNWS_GEOMETRY_PARAMS_LONG = (
    'minlatitude', 'maxlatitude', 'minlongitude', 'maxlongitude')

FDSNWS_GEOMETRY_PARAMS_SHORT = ('minlat', 'maxlat', 'minlon', 'maxlon')

FIXED_DATA_HEADER_SIZE = 48
DATA_ONLY_BLOCKETTE_SIZE = 8

DATA_ONLY_BLOCKETTE_NUMBER = 1000
MINIMUM_RECORD_LENGTH = 256

BLOCKETTE_SIZES = {
    100: 12,
    200: 52,
    201: 60,
    300: 60,
    310: 60,
    320: 64,
    390: 28,
    395: 16,
    400: 16,
    405: 6,
    500: 200,
    1000: 8,
    1001: 8
}


class Error(Exception):
    pass


class AuthNotSupported(Exception):
    pass


class TargetURL(object):
    def __init__(self, url, qp):
        self.__scheme = url.scheme
        self.__netloc = url.netloc
        self.__path = url.path.rstrip('query').rstrip('/')
        self.__qp = dict(qp)

    def wadl(self):
        path = self.__path + '/application.wadl'
        return urlparse.urlunparse((self.__scheme,
                                    self.__netloc,
                                    path,
                                    '',
                                    '',
                                    ''))

    def auth(self):
        path = self.__path + '/auth'
        return urlparse.urlunparse(('https',
                                    self.__netloc,
                                    path,
                                    '',
                                    '',
                                    ''))

    def post(self):
        path = self.__path + '/query'
        return urlparse.urlunparse((self.__scheme,
                                    self.__netloc,
                                    path,
                                    '',
                                    '',
                                    ''))

    def post_qa(self):
        path = self.__path + '/queryauth'
        return urlparse.urlunparse((self.__scheme,
                                    self.__netloc,
                                    path,
                                    '',
                                    '',
                                    ''))

    def post_params(self):
        return self.__qp.items()


class RoutingURL(object):
    def __init__(self, url, qp):
        self.__scheme = url.scheme
        self.__netloc = url.netloc
        self.__path = url.path.rstrip('query').rstrip('/')
        self.__qp = dict(qp)

    def get(self):
        path = self.__path + '/query'
        qp = [(p, v) for (p, v) in self.__qp.items() if p in GET_PARAMS]
        qp.append(('format', 'post'))
        query = urllib.urlencode(qp)
        return urlparse.urlunparse((self.__scheme,
                                    self.__netloc,
                                    path,
                                    '',
                                    query,
                                    ''))

    def post(self):
        path = self.__path + '/query'
        return urlparse.urlunparse((self.__scheme,
                                    self.__netloc,
                                    path,
                                    '',
                                    '',
                                    ''))

    def post_params(self):
        qp = [(p, v) for (p, v) in self.__qp.items() if p in POST_PARAMS]
        qp.append(('format', 'post'))
        return qp

    def target_params(self):
        return [(p, v) for (p, v) in self.__qp.items() if p not in GET_PARAMS]


class TextCombiner(object):
    def __init__(self):
        self.__text = ''

    def combine(self, text):
        if self.__text:
            self.__text = ''.join((self.__text, text))
        else:
            self.__text = '\n'.join((STATION_RESPONSE_TEXT_HEADER, text))

    def dump(self, fd):
        if self.__text:
            fd.write(self.__text)
            
            
class XMLCombiner(object):
    def __init__(self, qp):
        self.__et = None
        self.__qp = qp
        self.__geometry_par_type = get_geometry_par_type(qp)

    def __combine_element(self, one, other):
        mapping = {}

        for el in one:
            try:
                eid = (el.tag, el.attrib['code'], el.attrib['start'])
                mapping[eid] = el

            except KeyError:
                pass

        for el in other:
            
            # skip Sender, Source, Module, ModuleURI, Created elements of 
            # subsequent trees
            if el.tag in STATIONXML_RESOURCE_METADATA_ELEMENTS:
                continue
            
            # station coords: check lat-lon box, remove stations outside
            if self.__geometry_par_type is not None and \
                el.tag == STATIONXML_NETWORK_ELEMENT:

                remove_stations_outside_box(
                    el, self.__qp, self.__geometry_par_type)
            
            try:
                eid = (el.tag, el.attrib['code'], el.attrib['start'])

                try:
                    self.__combine_element(mapping[eid], el)

                except KeyError:
                    mapping[eid] = el
                    one.append(el)

            except KeyError:
                one.append(el)

    def combine(self, fd):
        if self.__et:
            self.__combine_element(self.__et.getroot(), ET.parse(fd).getroot())

        else:
            self.__et = ET.parse(fd)
            root = self.__et.getroot()
            
            # Note: this assumes well-formed StationXML
            # first StationXML tree: modify Source, Created
            try:
                source = root.find(STATIONXML_RESOURCE_METADATA_ELEMENTS[0])
                source.text = 'EIDA'
            except Exception:
                pass
            
            try:
                created = root.find(STATIONXML_RESOURCE_METADATA_ELEMENTS[1])
                created.text = datetime.datetime.utcnow().strftime(
                    '%Y-%m-%dT%H:%M:%S')
            except Exception:
                pass
            
            # remove Sender, Module, ModuleURI
            for tag in STATIONXML_RESOURCE_METADATA_ELEMENTS[2:]:
                el = root.find(tag)
                if el is not None:
                    root.remove(el)
                    
            # station coords: check lat-lon box, remove stations outside
            if self.__geometry_par_type is not None:
                
                networks = root.findall(STATIONXML_NETWORK_ELEMENT)
                for net in networks:
                    remove_stations_outside_box(
                        net, self.__qp, self.__geometry_par_type)
           

    def dump(self, fd):
        if self.__et:
            self.__et.write(fd)


class ArclinkParser(object):
    def __init__(self):
        self.postdata = ""
        self.failstr = ""

    def __parse_line(self, line):
        items = line.split()

        if len(items) < 2:
            self.failstr += "%s [syntax error]\n" % line
            return

        try:
            beg_time = datetime.datetime(*map(int, items[0].split(",")))
            end_time = datetime.datetime(*map(int, items[1].split(",")))

        except ValueError as e:
            self.failstr += "%s [invalid begin or end time: %s]\n" \
                            % (line, str(e))
            return

        network = 'XX'
        station = 'XXXXX'
        channel = 'XXX'
        location = '--'

        if len(items) > 2 and items[2] != '.':
            network = items[2]

            if len(items) > 3 and items[3] != '.':
                station = items[3]

                if len(items) > 4 and items[4] != '.':
                    channel = items[4]

                    if len(items) > 5 and items[5] != '.':
                        location = items[5]

        self.postdata += "%s %s %s %s %sZ %sZ\n" \
                         % (network,
                            station,
                            location,
                            channel,
                            beg_time.isoformat(),
                            end_time.isoformat())

    def parse(self, path):
        with open(path) as fd:
            for line in fd:
                line = line.rstrip()

                if line:
                    self.__parse_line(line)


class BreqParser(object):
    __tokenrule = "^\.[A-Z_]+[:]?\s"

    __reqlist = ("(?P<station>[\w?\*]+)",
                 "(?P<network>[\w?]+)",
                 "((?P<beg_2year>\d{2})|(?P<beg_4year>\d{4}))",
                 "(?P<beg_month>\d{1,2})",
                 "(?P<beg_day>\d{1,2})",
                 "(?P<beg_hour>\d{1,2})",
                 "(?P<beg_min>\d{1,2})",
                 "(?P<beg_sec>\d{1,2})(\.\d*)?",
                 "((?P<end_2year>\d{2})|(?P<end_4year>\d{4}))",
                 "(?P<end_month>\d{1,2})",
                 "(?P<end_day>\d{1,2})",
                 "(?P<end_hour>\d{1,2})",
                 "(?P<end_min>\d{1,2})",
                 "(?P<end_sec>\d{1,2})(\.\d*)?",
                 "(?P<cha_num>\d+)",
                 "(?P<cha_list>[\w?\s*]+)")

    def __init__(self):
        self.__rx_tokenrule = re.compile(BreqParser.__tokenrule)
        self.__rx_reqlist = re.compile("\s+".join(BreqParser.__reqlist))
        self.postdata = ""
        self.failstr = ""

    def __parse_line(self, line):
        m = self.__rx_reqlist.match(line)

        if m:
            d = m.groupdict()

            # catch two digit year inputs
            if d["beg_2year"]:
                if int(d["beg_2year"]) > 50:
                    d["beg_4year"] = "19%s" % d["beg_2year"]

                else:
                    d["beg_4year"] = "20%s" % d["beg_2year"]

            if d["end_2year"]:
                if int(d["end_2year"]) > 50:
                    d["end_4year"] = "19%s" % d["end_2year"]

                else:
                    d["end_4year"] = "20%s" % d["end_2year"]

            # some users have problems with time...
            if int(d["beg_hour"]) > 23:
                d["beg_hour"] = "23"

            if int(d["end_hour"]) > 23:
                d["end_hour"] = "23"

            if int(d["beg_min"]) > 59:
                d["beg_min"] = "59"

            if int(d["end_min"]) > 59:
                d["end_min"] = "59"

            if int(d["beg_sec"]) > 59:
                d["beg_sec"] = "59"

            if int(d["end_sec"]) > 59:
                d["end_sec"] = "59"

            try:
                beg_time = datetime.datetime(int(d["beg_4year"]),
                                             int(d["beg_month"]),
                                             int(d["beg_day"]),
                                             int(d["beg_hour"]),
                                             int(d["beg_min"]),
                                             int(d["beg_sec"]))

                end_time = datetime.datetime(int(d["end_4year"]),
                                             int(d["end_month"]),
                                             int(d["end_day"]),
                                             int(d["end_hour"]),
                                             int(d["end_min"]),
                                             int(d["end_sec"]))

            except ValueError as e:
                self.failstr += "%s [error: wrong begin or end time: %s]\n" \
                                % (line, str(e))
                return

            location = "*"
            cha_list = re.findall("([\w?\*]+)\s*", d["cha_list"])

            if len(cha_list) == int(d['cha_num'])+1:
                location = cha_list.pop()

            for channel in cha_list:
                self.postdata += "%s %s %s %s %sZ %sZ\n" \
                                 % (d["network"],
                                    d["station"],
                                    location,
                                    channel,
                                    beg_time.isoformat(),
                                    end_time.isoformat())

        else:
            self.failstr += "%s [syntax error]\n" % line

    def parse(self, path):
        with open(path) as fd:
            for line in fd:
                if self.__rx_tokenrule.match(line):
                    continue

                line = line.rstrip()

                if line:
                    self.__parse_line(line)


msglock = threading.Lock()


def msg(s, verbose=True):
    if verbose:
        with msglock:
            sys.stderr.write(s + '\n')
            sys.stderr.flush()

def get_geometry_par_type(qp):

    par_short_count = 0
    par_long_count = 0
    
    for (p, v) in qp.iteritems():
        
        if p in FDSNWS_GEOMETRY_PARAMS_SHORT:
            try:
                _ = float(v)
                par_short_count += 1
            except Exception:
                continue
            
        elif p in FDSNWS_GEOMETRY_PARAMS_LONG:
            try:
                _ = float(v)
                par_long_count += 1
            except Exception:
                continue
            
    if par_long_count == len(FDSNWS_GEOMETRY_PARAMS_LONG):
        par_type = 'long'
    elif par_short_count == len(FDSNWS_GEOMETRY_PARAMS_SHORT):
        par_type = 'short'
    else:
        par_type = None
    
    return par_type

def is_within_box(lat, lon, qp, geometry_par_type):

    if geometry_par_type == 'long':
        return (lat >= float(qp['minlatitude']) and 
            lat <= float(qp['maxlatitude']) and 
            lon >= float(qp['minlongitude']) and 
            lon <= float(qp['maxlongitude']))
    
    elif geometry_par_type == 'short':
        return (lat >= float(qp['minlat']) and lat <= float(qp['maxlat']) and
            lon >= float(qp['minlon']) and lon <= float(qp['maxlon']))
    
    else:
        return False

def remove_stations_outside_box(net, qp, geometry_par_type):
                    
    stations = net.findall(STATIONXML_STATION_ELEMENT)
                    
    for st in stations:
        lat = float(st.find(STATIONXML_LATITUDE_ELEMENT).text)
        lon = float(st.find(STATIONXML_LONGITUDE_ELEMENT).text)
                        
        if not is_within_box(lat, lon, qp, geometry_par_type):
            net.remove(st)
                            
def retry(urlopen, url, data, timeout, count, wait, verbose):
    n = 0

    while True:
        if n >= count:
            return urlopen(url, data, timeout)

        try:
            n += 1

            fd = urlopen(url, data, timeout)

            if fd.getcode() == 200 or fd.getcode() == 204:
                return fd

            msg("retrying %s (%d) after %d seconds due to HTTP status code %d"
                % (url, n, wait, fd.getcode()), verbose)

            fd.close()
            time.sleep(wait)

        except urllib2.HTTPError as e:
            if e.code >= 400 and e.code < 500:
                raise

            msg("retrying %s (%d) after %d seconds due to %s"
                % (url, n, wait, str(e)), verbose)

            time.sleep(wait)

        except (urllib2.URLError, socket.error) as e:
            msg("retrying %s (%d) after %d seconds due to %s"
                % (url, n, wait, str(e)), verbose)

            time.sleep(wait)


def fetch(url, cred, authdata, postlines, xc, tc, dest, timeout, retry_count,
          retry_wait, finished, lock, verbose):
    try:
        url_handlers = []

        if cred and url.post_qa() in cred:  # use static credentials
            query_url = url.post_qa()
            (user, passwd) = cred[query_url]
            mgr = urllib2.HTTPPasswordMgrWithDefaultRealm()
            mgr.add_password(None, query_url, user, passwd)
            h = urllib2.HTTPDigestAuthHandler(mgr)
            url_handlers.append(h)

        elif authdata:  # use the pgp-based auth method if supported
            wadl_url = url.wadl()
            auth_url = url.auth()
            query_url = url.post_qa()

            try:
                fd = retry(urllib2.urlopen, wadl_url, None, timeout,
                           retry_count, retry_wait, verbose)

                try:
                    root = ET.parse(fd).getroot()
                    ns = "{http://wadl.dev.java.net/2009/02}"
                    el = "resource[@path='auth']"

                    if root.find(".//" + ns + el) is None:
                        raise AuthNotSupported

                finally:
                    fd.close()

                msg("authenticating at %s" % auth_url, verbose)

                if not isinstance(authdata, bytes):
                    authdata = authdata.encode('utf-8')

                try:
                    fd = retry(urllib2.urlopen, auth_url, authdata, timeout,
                               retry_count, retry_wait, verbose)

                    try:
                        if fd.getcode() == 200:
                            up = fd.read()

                            if isinstance(up, bytes):
                                up = up.decode('utf-8')

                            try:
                                (user, passwd) = up.split(':')
                                mgr = urllib2.HTTPPasswordMgrWithDefaultRealm()
                                mgr.add_password(None, query_url, user, passwd)
                                h = urllib2.HTTPDigestAuthHandler(mgr)
                                url_handlers.append(h)

                            except ValueError:
                                msg("invalid auth response: %s" % up)
                                return

                            msg("authentication at %s successful"
                                % auth_url, verbose)

                        else:
                            msg("authentication at %s failed with HTTP "
                                "status code %d" % (auth_url, fd.getcode()))

                    finally:
                        fd.close()

                except (urllib2.URLError, socket.error) as e:
                    msg("authentication at %s failed: %s" % (auth_url, str(e)))
                    query_url = url.post()

            except (urllib2.URLError, socket.error, ET.ParseError) as e:
                msg("reading %s failed: %s" % (wadl_url, str(e)))
                query_url = url.post()

            except AuthNotSupported:
                msg("authentication at %s is not supported"
                    % auth_url, verbose)

                query_url = url.post()

        else:  # fetch data anonymously
            query_url = url.post()

        opener = urllib2.build_opener(*url_handlers)

        i = 0
        n = len(postlines)

        while i < len(postlines):
            if n == len(postlines):
                msg("getting data from %s" % query_url, verbose)

            else:
                msg("getting data from %s (%d%%..%d%%)"
                    % (query_url,
                       100*i/len(postlines),
                       min(100, 100*(i+n)/len(postlines))),
                    verbose)

            postdata = (''.join((p + '=' + v + '\n')
                                for (p, v) in url.post_params()) +
                        ''.join(postlines[i:i+n]))

            # msg("postdata:\n%s" % postdata, verbose)
            
            if not isinstance(postdata, bytes):
                postdata = postdata.encode('utf-8')

            try:
                fd = retry(opener.open, query_url, postdata, timeout,
                           retry_count, retry_wait, verbose)

                try:
                    if fd.getcode() == 204:
                        msg("received no data from %s" % query_url, verbose)

                    elif fd.getcode() != 200:
                        msg("getting data from %s failed with HTTP status "
                            "code %d" % (query_url, fd.getcode()))

                        break

                    else:
                        size = 0

                        content_type = fd.info().get('Content-Type')
                        content_type = content_type.split(';')[0]

                        if content_type == "application/vnd.fdsn.mseed":
                            
                            record_idx = 1
                            
                            # NOTE: cannot use fixed chunk size, because
                            # response from single node mixes mseed record
                            # sizes. E.g., a 4096 byte chunk could contain 7
                            # 512 byte records and the first 512 bytes of a 
                            # 4096 byte record, which would not be completed
                            # in the same write operation
                            while True:
                                
                                # read fixed header
                                buf = fd.read(FIXED_DATA_HEADER_SIZE)
                                if not buf:
                                    break
                                
                                record = buf
                                curr_size = len(buf)
                                
                                # get offset of data (value before last, 
                                # 2 bytes, unsigned short)
                                data_offset_idx = FIXED_DATA_HEADER_SIZE - 4
                                data_offset, = struct.unpack(
                                    '!H', 
                                    buf[data_offset_idx:data_offset_idx+2])
                                
                                if data_offset >= FIXED_DATA_HEADER_SIZE:
                                    remaining_header_size = data_offset - \
                                        FIXED_DATA_HEADER_SIZE
                                
                                elif data_offset == 0 :
                                    #msg("record %s: zero data offset" % (
                                        #record_idx))
                                    
                                    # This means that blockettes can follow,
                                    # but no data samples. Use minimum record 
                                    # size to read following blockettes. This
                                    # can still fail if blockette 1000 is after
                                    # position 256
                                    remaining_header_size = \
                                        MINIMUM_RECORD_LENGTH - \
                                            FIXED_DATA_HEADER_SIZE
                                
                                else:
                                    # Full header size cannot be smaller than 
                                    # fixed header size. This is an error.
                                    msg("record %s: data offset smaller than "\
                                        "fixed header length: %s, bailing "\
                                        "out" % (record_idx, data_offset))
                                    break
                                    
                                buf = fd.read(remaining_header_size)
                                if not buf:
                                    msg("remaining header corrupt in record "\
                                        "%s" % record_idx)
                                    break
                                
                                record += buf
                                curr_size += len(buf)
                                
                                # scan variable header for blockette 1000 
                                blockette_start = 0
                                b1000_found = False
                                
                                while (blockette_start < remaining_header_size):
                                    
                                    # 2 bytes, unsigned short
                                    blockette_id, = struct.unpack(
                                        '!H', 
                                        buf[blockette_start:blockette_start+2])
                                    
                                    # get start of next blockette (second 
                                    # value, 2 bytes, unsigned short)
                                    next_blockette_start, = struct.unpack(
                                        '!H', 
                                        buf[blockette_start+2:blockette_start+4])
                                    
                                    if blockette_id == \
                                        DATA_ONLY_BLOCKETTE_NUMBER:
                                        
                                        b1000_found = True
                                        break
                                    
                                    elif next_blockette_start == 0:
                                        # no blockettes follow
                                        msg("record %s: no blockettes follow "\
                                            "after blockette %s at pos %s" % (
                                            record_idx, blockette_id, 
                                            blockette_start))
                                        break
                                    
                                    else:
                                        blockette_start = next_blockette_start
                                
                                # blockette 1000 not found
                                if not b1000_found:
                                    msg("record %s: blockette 1000 not found,"\
                                        " stop reading" % record_idx)
                                    break
                                    
                                # get record size (1 byte, unsigned char)
                                record_size_exponent_idx = blockette_start + 6
                                record_size_exponent, = struct.unpack(
                                    '!B', 
                                    buf[record_size_exponent_idx:\
                                    record_size_exponent_idx+1])

                                remaining_record_size = \
                                    2**record_size_exponent - curr_size
                                
                                # read remainder of record (data section)
                                buf = fd.read(remaining_record_size)
                                if not buf:
                                    msg("cannot read data section of record "\
                                        "%s" % record_idx)
                                    break
                                
                                record += buf
                                with lock:
                                    dest.write(record)

                                size += len(record)
                                record_idx += 1

                        elif content_type == "text/plain":
                            
                            # this is the station service in text format
                            output = ''
                            while True:
                                buf = fd.readline()
                                
                                if not buf:
                                    break
                                
                                # skip header lines that start with '#'
                                # Note: first header line is inserted in
                                # TextCombiner class
                                if buf.startswith('#'):
                                    continue
                                    
                                output = ''.join((output, buf))
                                size += len(buf)
                                
                            tc.combine(output)

                        elif content_type == "application/xml":
                            fdread = fd.read
                            s = [0]

                            def read(self, *args, **kwargs):
                                buf = fdread(self, *args, **kwargs)
                                s[0] += len(buf)
                                return buf

                            fd.read = read
                            xc.combine(fd)
                            size = s[0]

                        else:
                            msg("getting data from %s failed: unsupported "
                                "content type '%s'" % (query_url,
                                                       content_type))

                            break

                        msg("got %d bytes (%s) from %s"
                            % (size, content_type, query_url), verbose)

                    i += n

                finally:
                    fd.close()

            except urllib2.HTTPError as e:
                if e.code == 413 and n > 1:
                    msg("request too large for %s, splitting"
                        % query_url, verbose)

                    n = -(n//-2)

                else:
                    msg("getting data from %s failed: %s"
                        % (query_url, str(e)))

                    break

            except (urllib2.URLError, socket.error, ET.ParseError) as e:
                msg("getting data from %s failed: %s"
                    % (query_url, str(e)))

                break

    finally:
        finished.put(threading.current_thread())


def route(url, qp, cred, authdata, postdata, dest, timeout, retry_count,
          retry_wait, maxthreads, verbose):
    threads = []
    running = 0
    finished = Queue.Queue()
    lock = threading.Lock()
    xc = XMLCombiner(qp)
    tc = TextCombiner()

    if postdata:
        query_url = url.post()
        postdata = (''.join((p + '=' + v + '\n')
                            for (p, v) in url.post_params()) +
                    postdata)

        if not isinstance(postdata, bytes):
            postdata = postdata.encode('utf-8')

    else:
        query_url = url.get()

    msg("getting routes from %s" % query_url, verbose)

    try:
        fd = retry(urllib2.urlopen, query_url, postdata, timeout, retry_count,
                   retry_wait, verbose)

        try:
            if fd.getcode() == 204:
                raise Error("received no routes from %s" % query_url)

            elif fd.getcode() != 200:
                raise Error("getting routes from %s failed with HTTP status "
                            "code %d" % (query_url, fd.getcode()))

            else:
                urlline = None
                postlines = []

                while True:
                    line = fd.readline()

                    if isinstance(line, bytes):
                        line = line.decode('utf-8')

                    if not urlline:
                        urlline = line.strip()

                    elif not line.strip():
                        if postlines:
                            target_url = TargetURL(urlparse.urlparse(urlline),
                                                   url.target_params())
                            threads.append(threading.Thread(target=fetch,
                                                            args=(target_url,
                                                                  cred,
                                                                  authdata,
                                                                  postlines,
                                                                  xc,
                                                                  tc,
                                                                  dest,
                                                                  timeout,
                                                                  retry_count,
                                                                  retry_wait,
                                                                  finished,
                                                                  lock,
                                                                  verbose)))

                        urlline = None
                        postlines = []

                        if not line:
                            break

                    else:
                        postlines.append(line)

        finally:
            fd.close()

    except (urllib2.URLError, socket.error) as e:
        raise Error("getting routes from %s failed: %s" % (query_url, str(e)))

    for t in threads:
        if running >= maxthreads:
            thr = finished.get(True)
            thr.join()
            running -= 1

        t.start()
        running += 1

    while running:
        thr = finished.get(True)
        thr.join()
        running -= 1

    xc.dump(dest)
    tc.dump(dest)


def main(args):
    qp = {}

    def add_qp(option, opt_str, value, parser):
        if option.dest == 'query':
            try:
                (p, v) = value.split('=', 1)
                qp[p] = v

            except ValueError:
                raise optparse.OptionValueError("%s expects parameter=value"
                                                % opt_str)

        else:
            qp[option.dest] = value

    parser = optparse.OptionParser(
            usage="Usage: %prog [-h|--help] [OPTIONS] -o file",
            version="%prog " + VERSION,
            add_help_option=False)

    parser.set_defaults(
            url="http://geofon.gfz-potsdam.de/eidaws/routing/1/",
            timeout=600,
            retries=10,
            retry_wait=60,
            threads=5)

    parser.add_option("-h", "--help", action="store_true", default=False,
                      help="show help message and exit")

    parser.add_option("-l", "--longhelp", action="store_true", default=False,
                      help="show extended help message and exit")

    parser.add_option("-v", "--verbose", action="store_true", default=False,
                      help="verbose mode")

    parser.add_option("-u", "--url", type="string",
                      help="URL of routing service (default %default)")

    parser.add_option("-y", "--service", type="string", action="callback",
                      callback=add_qp,
                      help="target service (default dataselect)")

    parser.add_option("-N", "--network", type="string", action="callback",
                      callback=add_qp,
                      help="network code or pattern")

    parser.add_option("-S", "--station", type="string", action="callback",
                      callback=add_qp,
                      help="station code or pattern")

    parser.add_option("-L", "--location", type="string", action="callback",
                      callback=add_qp,
                      help="location code or pattern")

    parser.add_option("-C", "--channel", type="string", action="callback",
                      callback=add_qp,
                      help="channel code or pattern")

    parser.add_option("-s", "--starttime", type="string", action="callback",
                      callback=add_qp,
                      help="start time")

    parser.add_option("-e", "--endtime", type="string", action="callback",
                      callback=add_qp,
                      help="end time")

    parser.add_option("-q", "--query", type="string", action="callback",
                      callback=add_qp, metavar="PARAMETER=VALUE",
                      help="additional query parameter")

    parser.add_option("-t", "--timeout", type="int",
                      help="request timeout in seconds (default %default)")

    parser.add_option("-r", "--retries", type="int",
                      help="number of retries (default %default)")

    parser.add_option("-w", "--retry-wait", type="int",
                      help="seconds to wait before each retry "
                           "(default %default)")

    parser.add_option("-n", "--threads", type="int",
                      help="maximum number of download threads "
                           "(default %default)")

    parser.add_option("-c", "--credentials-file", type="string",
                      help="URL,user,password file (CSV format) for queryauth")

    parser.add_option("-a", "--auth-file", type="string",
                      help="file that contains the auth token")

    parser.add_option("-p", "--post-file", type="string",
                      help="request file in FDSNWS POST format")

    parser.add_option("-f", "--arclink-file", type="string",
                      help="request file in ArcLink format")

    parser.add_option("-b", "--breqfast-file", type="string",
                      help="request file in breq_fast format")

    parser.add_option("-o", "--output-file", type="string",
                      help="file where downloaded data is written")

    (options, args) = parser.parse_args(args)

    if options.help:
        print(__doc__.split("Usage Examples", 1)[0], end="")
        parser.print_help()
        return 0

    if options.longhelp:
        print(__doc__)
        parser.print_help()
        return 0

    if args or not options.output_file:
        parser.print_usage()
        return 1

    if bool(options.post_file) + bool(options.arclink_file) + \
            bool(options.breqfast_file) > 1:
        msg("only one of (--post-file, --arclink-file, --breqfast-file) "
            "can be used")
        return 1

    try:
        cred = {}
        authdata = None
        postdata = None

        if options.credentials_file:
            with open(options.credentials_file) as fd:
                try:
                    for (url, user, passwd) in csv.reader(fd):
                        cred[url] = (user, passwd)

                except (ValueError, csv.Error):
                    raise Error("error parsing %s" % options.credentials_file)

        if options.auth_file:
            with open(options.auth_file) as fd:
                authdata = fd.read()

        if options.post_file:
            with open(options.post_file) as fd:
                postdata = fd.read()

        else:
            parser = None

            if options.arclink_file:
                parser = ArclinkParser()
                parser.parse(options.arclink_file)

            elif options.breqfast_file:
                parser = BreqParser()
                parser.parse(options.breqfast_file)

            if parser is not None:
                if parser.failstr:
                    msg(parser.failstr)
                    return 1

                postdata = parser.postdata

        url = RoutingURL(urlparse.urlparse(options.url), qp)
        dest = open(options.output_file, 'wb')

        route(url, qp, cred, authdata, postdata, dest, options.timeout,
              options.retries, options.retry_wait, options.threads,
              options.verbose)

    except (IOError, Error) as e:
        msg(str(e))
        return 1

    return 0


if __name__ == "__main__":
    __doc__ %= {"prog": sys.argv[0]}
    sys.exit(main(sys.argv[1:]))