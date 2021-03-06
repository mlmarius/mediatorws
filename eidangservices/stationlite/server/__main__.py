#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Launch stationlite server.

This file is part of the EIDA mediator/federator webservices.

"""

import argparse
import os

from eidangservices.stationlite.server.app import main as start_app

DEFAULT_DBFILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 
    '../example/db/stationlite_2017-10-20.db')


def main():
    
    parser = argparse.ArgumentParser(
        prog="python -m stationlite.server",
        description='Launch EIDA stationlite web service.')
    
    # required=True
    parser.add_argument('--port', type=int, help='Server port')
    parser.add_argument(
        '--db', type=str, default=DEFAULT_DBFILE, 
        help='Database (SQLite) file.')
    parser.add_argument(
        '--debug', action='store_true', default=False, 
        help="Run in debug mode.")

    args = parser.parse_args()

    start_app(debug=args.debug, port=args.port, dbfile=args.db)
    

if __name__ == "__main__":
    main()
