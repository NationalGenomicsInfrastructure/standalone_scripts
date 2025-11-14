import os
import glob
import re
import sys
import socket
import couchdb
import logging
import argparse
import yaml
import json
import distance
import operator
try:
    import ConfigParser
except ImportError:
    import configparser

CONFIG = {}

logger = logging.getLogger(__name__)



def associete_samples(samples_name, mode):
    couch        = setupServer(CONFIG)
    projects_db  = couch['projects']
    for doc_id in projects_db:
        #perform sanity check on statusDB project database
        if 'creation_time' not in projects_db[doc_id]:
            continue
        if 'details' not in projects_db[doc_id] or 'customer_project_reference' not in projects_db[doc_id]['details'] or \
            'project_id' not in projects_db[doc_id]:
                continue
        project = projects_db[doc_id]
        if 'samples' not in project:
            continue
        for sample in project['samples']:
            if 'customer_name' in project['samples'][sample] and 'details' in project['samples'][sample] and \
            'status_(manual)' in project['samples'][sample]['details']:
                sample_user_name = project['samples'][sample]['customer_name']
                sample_NGI_name  = project['samples'][sample]['scilife_name']
                status           = project['samples'][sample]['details']['status_(manual)']
                if mode == 'user2NGI':
                    if sample_user_name in  samples_name:
                        print("{},{},{}".format(sample_user_name.encode('ascii', 'ignore'), sample_NGI_name, status))
                else:
                    if sample_NGI_name in  samples_name:
                        print("{},{},{}".format(sample_NGI_name, sample_user_name.encode('ascii', 'ignore'), status))



def associate_projects(projects_name, mode):
    couch        = setupServer(CONFIG)
    projects_db  = couch['projects']

    user2NGI_samples_names = {}
    NGI2user_samples_names = {}
    for doc_id in projects_db:
        #perform sanity check on statusDB project database
        if 'creation_time' not in projects_db[doc_id]:
            continue
        if 'details' not in projects_db[doc_id] or 'customer_project_reference' not in projects_db[doc_id]['details'] or \
            'project_id' not in projects_db[doc_id]:
                continue
        #check the projects
        project = projects_db[doc_id]
        user_project_name = projects_db[doc_id]['details']['customer_project_reference']
        NGI_project_name  = projects_db[doc_id]['project_id']

        if project['project_id'] in projects_name:
            for sample in project['samples']:
                sample_user_name = project['samples'][sample]['customer_name']
                sample_NGI_name  = project['samples'][sample]['scilife_name']
                status           = project['samples'][sample]['details']['status_(manual)']
                if sample_user_name not in user2NGI_samples_names:
                    user2NGI_samples_names[sample_user_name] = []
                user2NGI_samples_names[sample_user_name].append([sample_NGI_name, status, user_project_name, NGI_project_name])
                if sample_NGI_name not in NGI2user_samples_names:
                    NGI2user_samples_names[sample_NGI_name] = []
                NGI2user_samples_names[sample_NGI_name].append([sample_user_name, status, user_project_name, NGI_project_name])

    if mode == 'user2NGI':
        for sample in user2NGI_samples_names:
            print("{}".format(sample.encode('ascii', 'ignore')), end=' ') # handle unicode in sample names
            for NGI_id in user2NGI_samples_names[sample]:
                print(" --- {},{},{},{}".format(NGI_id[0].encode('ascii', 'ignore'),NGI_id[1],NGI_id[2],NGI_id[3]), end=' ')
            print("")
    else:
        for sample in NGI2user_samples_names:
            sys.stdout.write("{}".format(sample))
            for user_id in NGI2user_samples_names[sample]:
                sys.stdout.write(" --- {},{},{},{}".format(user_id[0].encode('ascii', 'ignore'),user_id[1],user_id[2],user_id[3]))
            print("")




def setupServer(conf):
    db_conf = conf['statusdb']
    url="https://{0}:{1}@{2}".format(db_conf['username'], db_conf['password'], db_conf['url'])
    return couchdb.Server(url)



def load_yaml_config(config_file):
    """Load YAML config file

    :param str config_file: The path to the configuration file.

    :returns: A dict of the parsed config file.
    :rtype: dict
    :raises IOError: If the config file cannot be opened.
    """
    if type(config_file) is file:
        CONFIG.update(yaml.load(config_file) or {})
        return CONFIG
    else:
        try:
            with open(config_file, 'r') as f:
                content = yaml.load(f)
                CONFIG.update(content)
                return content
        except IOError as e:
            e.message = "Could not open configuration file \"{}\".".format(config_file)
            raise e


def user2NGI(args):
    if args.project is not None:
        associate_projects(args.project, args.mode)
    else:
        associete_samples(args.sample, args.mode)





def main(args):
    configuration_file = args.config
    projects_name      = args.project
    load_yaml_config(configuration_file)
    if args.project != None and args.sample != None: #Mutually exclusive arguments
        sys.exit("Only one between --project and --sample can be specified")

    if args.project is not None:
        associate_projects(args.project, args.mode)
    else:
        associete_samples(args.sample, args.mode)


    #findNGISampleNames("2014-02321")
    #findNGISampleNames("2153-08D")
    #findUserSampleNames(projects_name)





if __name__ == '__main__':
    parser = argparse.ArgumentParser("""This scripts connects to project database in statusDB and tries to associate user names to NGI names and vice-versa""")
    parser.add_argument('--config', help="cauchdb configuration file", type=str,  required=True)
    parser.add_argument('--mode', help="specifies if we want the user2NGI or the NGI2user convertion", required=True, choices=('user2NGI', 'NGI2user') )
    parser.add_argument('--project', help="project name. If specified returns all samples associated to this project and the user-NGI convertion outputs also the status of the sample)", type=str, action='append')
    parser.add_argument('--sample', help="Sample name. If specified returns a list of the samples the associated user/NGI names", type=str, action='append')
    args = parser.parse_args()
    main(args)
