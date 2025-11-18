#!/usr/bin/env python

import couchdb
import json
import argparse
import logbook
import sys
import os
import yaml
import base64
from Crypto.Cipher import AES


from couchdb import PreconditionFailed

#Set up logging
l = logbook.Logger('CouchDB-Replicator')

class AESDecrypt():
    def __init__(self, key):
        self.key=key
    def decrypt(self, enc):
        enc = base64.b64decode(enc)
        iv = enc[:AES.block_size]
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return self._unpad(cipher.decrypt(enc[AES.block_size:]))

    def _unpad(self, s):
        return s[:-ord(s[len(s)-1:])]

class Config(object):
    """Singleton class that holds the confiuration for the CouchDB replicator.
    """

    _instance = None

    def __new__(self, *args, **kwargs):
        if not self._instance:
            self._instance = super(Config, self).__new__(self, *args, **kwargs)
        return self._instance


    def __init__(self, config_file=None):
        config= None
        try:
            if not config_file:
                config_file = os.path.join(os.environ['HOME'], '.couchrc')
            with open(config_file, 'r') as f:
                config= yaml.load(f)

            self.login=config.get('replication').get('login')
            decrypt=AESDecrypt("{}_keys".format(self.login))
            self.password=decrypt.decrypt(config.get('replication').get('password'))
            self.source = "https://{}:{}@{}".format(self.login, self.password, config.get('replication').get('SOURCE'))
            self.destination= "https://{}:{}@{}".format(self.login, self.password, config.get('replication').get('DESTINATION'))
        except:
            l.error("Please make sure you've created your own configuration file \
                (i.e: ~/.couchrc), and that it contains a source and a destination servers")
            sys.exit(-1)

        self.exceptions=config.get('exceptions', [])

        self.roles = {"members": config.get("roles",{}).get("members", []),
                      "admins": config.get("roles",{}).get("members", [])
                      }


def _get_databases_info(source, destination, skip=None):
    """Returns a tuple containing a python representation of source and destination
    couchDB instances. It also returns a list of the databases in both instances
    (excluding the _replicator database).
    """
    if not skip:
        skip=[]
    s_couch = couchdb.Server(source)
    d_couch = couchdb.Server(destination)
    _, _, s_dbs = s_couch.resource.get_json('_all_dbs')
    _, _, d_dbs = d_couch.resource.get_json('_all_dbs')

    l.info("Databases in the source CouchDB instance: {}".format(', '.join(s_dbs)))
    l.info("Databases in the destination CouchDB instance: {}".format(', '.join(d_dbs)))

    #We don't want to replicate the replicator DB, and want to skip the databases in skip list
    skip.append('_replicator')
    for db in skip:
        try:
            s_dbs.remove(db)
        except ValueError:
            pass
        try:
            d_dbs.remove(db)
        except ValueError:
            pass

    return s_couch, d_couch, s_dbs, d_dbs


def _setup_continuous(source, destination, copy_security):
    """Set up a continuous replication of all databases in source to destination.
    """
    s_couch, d_couch, s_dbs, d_dbs = _get_databases_info(source, destination)

    #For each DB in the source CouchDB instance, create a replication document
    #and get its _security object to put it in the destination database
    for db in s_dbs:
        _, _, security = s_couch[db].resource.get_json('_security')
        doc = {
                'name': '{}_rep'.format(db),
                'source': '{}/{}/'.format(source, db),
                'target': '{}/{}/'.format(destination, db),
                'continuous': True
        }
        s_rep = s_couch['_replicator']

        #Create the DB in the destination if not present
        try:
            d_couch.create(db)
            l.info("Created {} database in destination".format(db))
        except PreconditionFailed:
            l.info("Database {} already existing in the destination, not creating it".format(db))

        #Put the replicator document in source and set security object in destination
        l.info("Putting replicator document in _replicator database of source")
        s_rep.create(doc)
        if copy_security:
            l.info("Copying security object to {} database in destination".format(db))
            d_couch[db].resource.put('_security', security)

    l.info("DONE!")


def _clone(source, destination, copy_security, with_exceptions=False, skip=[]):
    """Creates a complete clone of source in destination.

    WARNING: This action will remove ALL content from destination.
    """
    l.info("Performing a complete clone from source to destination")
    s_couch, d_couch, s_dbs, d_dbs = _get_databases_info(source, destination, skip)
    config = Config()

    #Delete all databases in destination
    l.info("Removing all databases from destination")
    for db in d_dbs:
        d_couch.delete(db)

    #Create all databases abailable in source to destination. Copy data and
    #permissions
    l.info("Re-creating databases from source into destination")
    for db in s_dbs:
        #The users database is never deleted
        if not db == '_users':
            d_couch.create(db)
        _, _, security = s_couch[db].resource.get_json('_security')
        source_db = '/'.join([source, db])
        dest_db = '/'.join([destination, db])
        l.info("Copying data from {} in source to destination".format(db))
        d_couch.replicate(source_db, dest_db)
        if copy_security:
            l.info("Copying security object to {} database in destination".format(db))
            d_couch[db].resource.put('_security', security)
        if with_exceptions:
            exceptions = config.exceptions
            if not exceptions:
                l.warn("--with-exceptions option was present, but didn't find " \
                        "any EXCEPTIONS list in your .couchrc file.")
            else:
                l.info("--with-exceptions option was present, removing following documents: {}".format(", ".join(exceptions)))
                for exception in exceptions:
                    try:
                        d_couch[db].delete(d_couch[db].get(exception))
                    except:
                        l.warn("Document {} not found, not deleteing".format(exception))


    l.info("DONE!")


def _set_roles(server):
    """Apply the list of roles present in .couchrc to all databases in the server.
    """
    security_obj = {"admins": {
                        "names":[],
                        "roles":[]
                        },
                    "members": {
                        "names":[],
                        "roles":[]
                        }
                    }

    config = Config()
    security_obj['admins']['roles'] = config.roles['admins']
    security_obj['members']['roles'] = config.roles['members']

    s_couch, d_couch, s_dbs, d_dbs = _get_databases_info(source, destination, None)
    l.info("Setting roles to destination databases: {}".format(str(security_obj)))
    for db in d_dbs:
        d_couch[db].resource.put('_security', security_obj)




if __name__ == "__main__":

    DESCRIPTION = """Set up complete one-way replication for CouchDB.

    Use this script if you want to configure a stage database that will have the
    exact same content of your production database.

    To do so, the script creates a replication document for each database in the
    source CouchDB instance that replicates such database (in continuous mode)
    to the destination database.

    Security object (permissions per database), are put to the destination databases.
    """

    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('action', type=str, help = "Action to perform, either \
            configure continuous replication (continuous) or punctual clone (clone)")
    parser.add_argument('--source', type=str, help = "Source CouchDB instance, \
            with the credentials included in the URL. I.E: https://admin:passw@source_db")
    parser.add_argument('--destination', type=str, help = "Destination CouchDB instance, \
            with the credentials included in the URL. I.E: https://admin:passw@destination_db")
    parser.add_argument('--no-security', action='store_const', const=True, \
            help='Do not copy security objects')
    parser.add_argument('--with-exceptions', action='store_const', const=True, \
            help='List of files to be deleted from the DataBases after being copied. ' \
            'To be specified in your .couchrc file')
    parser.add_argument('--set-roles', action='store_const', const=True, \
            help='List of roles to apply to  each database after copied. Only if' \
            '--no-security is present.')
    parser.add_argument('--skip', nargs="+", type=str,
            help=('List of databases to skip during the replication. '
                  'They will remain intact in the destination database'))

    args = parser.parse_args()
    source = args.source
    destination = args.destination
    copy_security = False if args.no_security else True
    action = args.action
    config = Config()

    if not all([source, destination]):
        source = config.source
        destination = config.destination

    actions = ['continuous', 'clone']
    if action not in actions:
        raise ValueError("Action not recognised, please choose between %s" % \
                ', '.join(actions))
    l.info("Starting replication - source: {}, destination: {}".format( \
            source.split('@')[-1], destination.split('@')[-1]))
    if action == "continuous":
        _setup_continuous(source, destination, copy_security)
    else:
        _clone(source, destination, copy_security, with_exceptions=args.with_exceptions, skip=args.skip)
        if args.set_roles:
            if not args.no_security:
                l.warn('--set-roles option only takes effect if applied together ' \
                        'with --no-security. Ignoring it')
            else:
                _set_roles(destination)
