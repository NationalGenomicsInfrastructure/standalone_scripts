import os
import glob
import re
import sys
import argparse
import random
import requests
import json
import datetime
import shutil
from datetime import  date



#Static list of all NeoPrep projects. As NeoPrep is discontinued this is a stable list.
NeoPrepProjects = ["P6971", "P6810", "P6651", "P6406", "P6254", "P6252", "P6152", "P5951", "P5514", "P5503", "P5476", "P5470", "P5370", "P5364", "P5301", "P5206", "P5201", "P5151", "P4903", "P4805", "P4753", "P4751", "P4729", "P4710", "P4651", "P4552", "P4454", "P4453", "P4401", "P4353", "P4206", "P4105", "P4056", "P4055", "P4004", "P3966", "P3719", "P3452", "P3451", "P2954", "P2806", "P2703", "P2477", "P2468", "P2456", "P2282", "P1888"]
ToExludeProjectForReallyGoodReasons=["P4752"]

def duplicate_trends(args):
    """Fetch all project and start to loop over them
    """
    token = args.token
    url   = args.url
    session = requests.Session()
    headers = {'X-Charon-API-token': token, 'content-type': 'application/json'}
    duplications_per_date = {}
    projects_per_date = {}
    projects = {}
    for project in  session.get(url+'/api/v1/projects', headers=headers).json()['projects']:
        if project['sequencing_facility'] != 'NGI-S':
            continue
        if "G.A16" in project['name'] or "G.A17" in project['name']:
            continue
        pid = project['projectid'] #project id
        #if pid in NeoPrepProjects or pid in ToExludeProjectForReallyGoodReasons:
        #    continue
        request = session.get(url+'/api/v1/samples/{}'.format(pid), headers=headers)
        if request.status_code != 200:
            print(pid)
            continue
        for sample in request.json()['samples']:
            if sample.get('analysis_status') != 'ANALYZED':
                continue
            if 'duplication_pc' not in sample:
                continue
            if sample['duplication_pc'] == 0:
                continue
            if project['name'] not in projects:
                projects[project['name']] = 0
            #store this a analysed proejcts
            #now fetch sample runs
            sid = sample['sampleid']
            dup_rate = sample['duplication_pc']
            oldest_run_date =  date.today() # no run can be older than today and being analysed
            for sample_run in session.get(url+ '/api/v1/seqruns/{}/{}'.format(pid, sid), headers=headers).json()['seqruns']:
                rid = sample_run['seqrunid']
                sequencing_start_date = rid.split("_")[0] #first 6 digit are the date
                year  = int("20" + sequencing_start_date[0:2])
                month = int(sequencing_start_date[2:4])
                day   = int(sequencing_start_date[4:6])
                if oldest_run_date > datetime.date(year, month, day):
                    oldest_run_date = datetime.date(year, month, day)
            #at this point I have the older run date
            if oldest_run_date not in duplications_per_date:
                duplications_per_date[oldest_run_date] = []
            duplications_per_date[oldest_run_date].append(dup_rate)
            if oldest_run_date not in projects_per_date:
                projects_per_date[oldest_run_date] = {}
            if pid not in projects_per_date[oldest_run_date]:
                projects_per_date[oldest_run_date][pid] = [1,dup_rate]
            else:
                projects_per_date[oldest_run_date][pid][0] += 1
                projects_per_date[oldest_run_date][pid][1] += dup_rate
        if len(duplications_per_date) > 0:
            continue
    for cur_date in sorted(duplications_per_date):
        average = sum((duplications_per_date[cur_date]))/float(len(duplications_per_date[cur_date]))
        sys.stdout.write("{} {} {} ".format(cur_date, average, len(duplications_per_date[cur_date])))
        for pid in projects_per_date[cur_date]:
            num_samples = projects_per_date[cur_date][pid][0]
            average_dup_rate_proj =  projects_per_date[cur_date][pid][1]/float(projects_per_date[cur_date][pid][0])
            sys.stdout.write("({},{},{}) ".format(pid,num_samples,average_dup_rate_proj))
        sys.stdout.write("\n")
    for project in projects:
        print(project)

def compute_human_genomes(args):
    """Fetch all project and start to loop over them
    """
    token = args.token
    url   = args.url
    session = requests.Session()
    headers = {'X-Charon-API-token': token, 'content-type': 'application/json'}
    duplications_per_date = {}
    projects_per_date = {}
    projects = {}
    samples_without_autosome_cov = 0
    total_coverage = 0
    total_samples  = 0
    for project in  session.get(url+'/api/v1/projects', headers=headers).json()['projects']:
        if project['sequencing_facility'] != 'NGI-S':
            continue
        pid = project['projectid'] #project id
        request = session.get(url+'/api/v1/samples/{}'.format(pid), headers=headers)
        for sample in request.json()['samples']:
            if sample.get('analysis_status') != 'ANALYZED':
                continue
            if 'duplication_pc' not in sample:
                continue
            if sample['duplication_pc'] == 0:
                continue
            if project['name'] not in projects:
                projects[project['name']] = 0
            #store this a analysed proejcts
            #now fetch sample runs
            sid = sample['sampleid']
            dup_rate = sample['duplication_pc']
            coverage_field = 'total_autosomal_coverage'
            if sample[coverage_field] == 0:
                coverage_field = 'target_coverage'
                samples_without_autosome_cov += 1
            total_coverage += sample[coverage_field]
            total_samples  += 1
    print("TOTAL SAMPLES {}".format(total_samples))
    print("TOTAL SAMPLES no cov {}".format(samples_without_autosome_cov))
    print("TOTAL COVERAGE {}".format(total_coverage))
    print("AVERAGE COVERAGE PER SAMPLE {}".format(total_coverage/total_samples))
    print("NUMBER OF 30X HG EQUVALENTS {}".format(total_coverage/30))



if __name__ == '__main__':
    parser = argparse.ArgumentParser("""This scripts connects to charon and fetches information about duplication rates for all human sample we are able to find. For each sample approaximates the sequencing data to the most recent sequencing run. It can be used also to compute the total amount of Whole Human Genomes sequenced by NGI-S.
        """)
    # general options
    parser.add_argument("-t", "--token", dest="token", default=os.environ.get('CHARON_API_TOKEN'),
            help="Charon API Token. Will be read from the env variable CHARON_API_TOKEN if not provided")
    parser.add_argument("-u", "--url", dest="url", default=os.environ.get('CHARON_BASE_URL'),
            help="Charon base url. Will be read from the env variable CHARON_BASE_URL if not provided")
    args = parser.parse_args()
    if not args.token :
        print( "No valid token found in arg or in environment. Exiting.")
        sys.exit(-1)
    if not args.url:
        print( "No valid url found in arg or in environment. Exiting.")
        sys.exit(-1)

    compute_human_genomes(args)
    duplicate_trends(args)
