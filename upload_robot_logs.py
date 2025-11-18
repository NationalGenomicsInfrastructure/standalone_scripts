"""
Script used to upload robot log messages to statusdb. Works either with stdin or a file.

Denis Moreno, Joel Gruselius
Scilifelab
2016-03-04
"""
import logging
import logging.handlers
import argparse
import sys
import datetime
from ibmcloudant import CouchDbSessionAuthenticator, cloudant_v1
import yaml
import socket

def save_to_statusdb(couch, db, message, args):
    data={'message':message}
    data['timestamp']=datetime.datetime.now().isoformat()
    data['instrument_name']=args.name

    couch.post_document(db=db, document=data).get_result()


def read_message(args):
    if args.input_file:
        #read from input file
        with open(args.input_file, 'rb') as inp:
            message=inp.read()
    else:
        #read from stdin
        message=sys.stdin.read().rstrip()

    return message


def setupServer(conf):
    db_conf = conf['statusdb']
    url=f"https://{db_conf['url'])}"
    couch = cloudant_v1.CloudantV1(
        authenticator=CouchDbSessionAuthenticator(
            db_conf['username'], db_conf['password']
        )
    )
    couch.set_service_url(url)
    return couch

def setupLog(name, logfile, log_level=logging.INFO, max_size=209715200, nb_files=5):
    mainlog = logging.getLogger(name)
    mainlog.setLevel(level=log_level)
    mfh = logging.handlers.RotatingFileHandler(logfile, maxBytes=max_size, backupCount=nb_files)
    mft = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    mfh.setFormatter(mft)
    mainlog.addHandler(mfh)
    return mainlog

def main(args):
    mainlog=setupLog(name="upload_robot_logs", logfile=args.logfile, nb_files=1)
    mainlog.info("Starting uploading from instrument {}".format(args.name))
    #read the configuration
    with open(args.conf) as conf_file:
        conf=yaml.load(conf_file)

    couch=setupServer(conf)
    db = conf['statusdb']['instrument_logs_db']

    message=read_message(args)
    mainlog.info("Read message : {}".format(message))
    try:
        save_to_statusdb(couch, db, message, args)
    except Exception:
        mainlog.error("Failed to upload to statusdb : {}".format(sys.exc_info()[0]))
    else:
        mainlog.info("Uploaded message {} from {} to statusdb".format(message, args.name))




if __name__=="__main__":
    usage = "Usage:       upload_robot_logs.py [options]"
    parser = argparse.ArgumentParser(description='Upload any message to statusdb.', usage=usage)

    parser.add_argument("-f", "--file", dest = "input_file", help = ("file",
                      " to read. its contents will be uploaded to satusdb as a message"))

    parser.add_argument("-n", "--name", dest = "name", help = (
                      "name of the instrument. Default is the host name"), default=socket.gethostname())

    parser.add_argument("-l", "--logfile", dest = "logfile", help = ("log file",
                      " that will be used. Default is ./statusdb_upload.log "), default="statusdb_upload.log")

    parser.add_argument("-c", "--conf", dest="conf",
    default='statusdb.yaml',
    help = "Config file.  Default: ./statusdb.yaml")

    args = parser.parse_args()
    main(args)
