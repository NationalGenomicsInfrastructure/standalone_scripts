"""
Script used to upload biomek log messages to statusdb. Adapted from upload_robot_logs.py
"""
import logging
import logging.handlers
import argparse
import os
from ibmcloudant import CouchDbSessionAuthenticator, cloudant_v1
import yaml
import re
import sys

error_line_regex = re.compile(r"^(?=\d{2}/\d{1,2}/\d{4} \d{2}:\d{2}:\d{2},\b)")

def create_doc_from_log_file(doc_option, handle, couch, log_file_path, db=None):

    error_lines = ""

    if doc_option == "create":
        doc = {"run_finished": False}
        doc["file_name"] = handle
    
        with open(os.path.join(log_file_path, handle), "r", encoding="utf-8-sig") as inp:
            contents=inp.readlines()

            for line in contents:
                if line.startswith("Method = "):
                    doc["method"] = line.split("Method = ")[1].strip()
                elif line.startswith("Started "):
                    doc["start_time"]=line.split("Started ")[1].strip()
                elif line.startswith("Unit serial number ="):
                    doc["inst_id"]=line.split("Unit serial number =")[1].strip()
                elif re.match(error_line_regex, line):
                    error_lines += line
                    if 'Run ended.' in line:
                        doc["run_finished"] = True          
            doc["errors"] = error_lines
    elif doc_option == "update":
        doc = couch.get_document(db=db, doc_id=handle).get_result()
        with open(os.path.join(log_file_path, doc["file_name"]), "r", encoding="utf-8-sig") as inp:
            contents=inp.readlines()
            for line in contents:
                if re.match(error_line_regex, line):
                    error_lines += line
                    if "Run ended." in line:
                        doc["run_finished"] = True
            doc["errors"] = error_lines
    return doc


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
    mainlog=setupLog(name="upload_biomek_logs", logfile=args.logfile, nb_files=1)
    mainlog.info("Starting upload")
    #read the configuration
    with open(args.conf) as conf_file:
        conf = yaml.safe_load(conf_file)

    couch = setupServer(conf["couchdb_credentials"])
    inst_id = conf["inst_id"]
    biomek_logs_db = "biomek_logs"

    db_view_run_finished = couch.post_view(
        db=biomek_logs_db,
        ddoc="names",
        view="run_finished",
        include_docs=False,
    ).get_result()["rows"]
    
    run_finished_dict = {}
    for row in db_view_run_finished:
        run_finished_dict[(row["key"][0], row["key"][1])] = (row["value"], row["id"])

    log_files_list = os.listdir(args.log_file_path)
    logs_to_create = []
    logs_to_update = []
    save_docs = []
    for fname in log_files_list:
        if fname.startswith("Errors"):
            if ((fname, inst_id) not in run_finished_dict):
                logs_to_create.append(fname)
            elif (run_finished_dict[(fname, inst_id)][0] == False):
                logs_to_update.append(run_finished_dict[(fname, inst_id)][1])


    for fname in logs_to_create:
        save_docs.append(create_doc_from_log_file('create', fname, couch, log_file_path=args.log_file_path))
    
    for doc_id in logs_to_update:
        save_docs.append(create_doc_from_log_file('update', doc_id, couch, log_file_path=args.log_file_path, db=biomek_logs_db))
    try:
        couch.post_bulk_docs(
            db=biomek_logs_db,
            bulk_docs=cloudant_v1.BulkDocs(docs=save_docs, new_edits=True),
        ).get_result()
    except Exception:
        mainlog.error("Failed to upload to statusdb : {}".format(sys.exc_info()[0]))
    else:
        for fname in logs_to_create:
            mainlog.info(f"Uploaded file {fname}")
        for fname in logs_to_update:
            mainlog.info(f"Updated docid {fname}")



if __name__=="__main__":
    usage = "Usage:       upload_biomek_logs.py [options]"
    parser = argparse.ArgumentParser(description='Upload biomek logs to statusdb.', usage=usage)

    parser.add_argument("-p", "--log_file_path", dest = "log_file_path", help = ("log file",
                      r" path to read the log file from. Default is C:\Users\Public\Documents\Biomek5\Logs"), default=r"C:\Users\Public\Documents\Biomek5\Logs")

    parser.add_argument("-l", "--logfile", dest = "logfile", help = ("log file",
                      " that will be used. Default is ./statusdb_upload.log "), default="statusdb_upload.log")

    parser.add_argument("-c", "--conf", dest="conf",
    default='./biomek_upload_conf.yaml',
    help = "Config file.  Default: ./biomek_upload_conf.yaml")

    args = parser.parse_args()
    main(args)
