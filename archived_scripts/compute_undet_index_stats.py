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


class Indexes:

    #indexes_by_kit looks like:
    #Kit_name:
    #   i7_index1:
    #       index_name: index_seq
    #       ...
    #   i5_index2:
    indexes_by_kit = {}
    #indexes looks like:
    #index_seq: ((index_name, index_type, kit_name), ....)
    indexes = {}

    def __init__(self, indexes_file):
        try:
            with open(indexes_file, 'r') as f:
                self.indexes_by_kit = yaml.load(f)
        except IOError as e:
                e.message = "Could not open configuration file \"{}\".".format(indexes_file)
                raise e
        #now create a more index centric object
        for kit_type in self.indexes_by_kit: #for each kit type
            if kit_type not in self.indexes_by_kit:
                print("file {} badly fomatted".format(indexes_file))
                return
            for index_type in self.indexes_by_kit[kit_type]: # for each type of indexes
                for index_name, index_seq in self.indexes_by_kit[kit_type][index_type].items():
                    index_obj = {'name': index_name, 'index_type': index_type, 'kit_type': kit_type}
                    self._add_index(index_seq, index_obj)

    #computes reverse complement
    def _reverse_complement(self, index):
        for base in index:
            if base not in 'ATCGNatcgn':
                print("Error: NOT a DNA sequence")
                return None
        complement_dict = {"A":"T", "C":"G", "G":"C", "T":"A", "N":"N", "a":"t", "c":"g", "g":"c", "t":"a", "n":"n" }
        return "".join([complement_dict[base] for base in reversed(index)])

    #check if index exists in the  indexes list
    def is_index(self, index):
        if index in self.indexes or self._reverse_complement(index) in self.indexes:
            return True
        else:
            return False

    def _add_index(self, index_seq, index_obj):
        index_to_modify = ""
        if index_seq in self.indexes:
            index_to_modify = index_seq
        elif self._reverse_complement(index_seq) in self.indexes:
            index_to_modify = self._reverse_complement(index_seq)
        else:
            index_to_modify = index_seq
            self.indexes[index_to_modify] = []
        #add the information
        self.indexes[index_to_modify].append(index_obj)




    #returns all kits
    def return_kits(self):
        kits = []
        for kit_type in self.indexes_by_kit:
            kits.append(kit_type)
        return kits

    #still to be defined
    def check_left_shift_conflicts(self):
        #checks if indexes from the same library after a left shift are conflicting
        for kit_type in self.indexes_by_kit: #for each lib kit type
            for index_type in self.indexes_by_kit[kit_type]: # for each type of indexes
                for index_name, index_seq in self.indexes_by_kit[kit_type][index_type].items():
                    fake_index = index_seq[1:] + "A"
                    for index_name_check, index_seq_check in self.indexes_by_kit[kit_type][index_type].items():
                        hamming_dist = distance.hamming(index_seq_check, fake_index)
                        if hamming_dist <= 2:
                            print("{} {} {} {} {}".format(index_seq, index_seq_check, fake_index, hamming_dist, kit_type))



def get_FC_type(FCid):
    FC_type = ""
    if "ST-" in FCid:
        FC_type = "HiSeqX"
    elif "000000000-" in FCid:
        FC_type = "MiSeq"
    else:
        FC_type = "HiSeq2500"
    return FC_type

import time
from datetime import  date

def check_single_sample_lanes(instrument_type):
    couch=setupServer(CONFIG)
    flowcell_db = couch["x_flowcells"]
    flowcell_docs = {}

    for fc_doc in flowcell_db:
        try:
            undetermined = flowcell_db[fc_doc]["Undetermined"]
        except KeyError:
            continue
        flowcell_docs[flowcell_db[fc_doc]["RunInfo"]["Id"]] = fc_doc

    undet_stats = {}
    indexes = {}
    date_limit = date(16,3,1)
    for FCid in sorted(flowcell_docs):
        # first check that I have all necessary info to extract information
        fc_doc = flowcell_docs[FCid]
        FC_type = get_FC_type(FCid)
        #if a instrument type is specifed process only FCs run on that instrument
        if instrument_type is not None:
            if instrument_type != FC_type:
                continue
        instrument_name = flowcell_db[fc_doc]['RunInfo']['Instrument']
        if instrument_name not in undet_stats:
            undet_stats[instrument_name] = {}
        #this is working only HiSeqX
        #only recent runs

        start_date_string = flowcell_db[fc_doc]['RunInfo']['Date']
        year = start_date_string[0:2]
        month = start_date_string[2:4]
        day = start_date_string[4:6]
        fc_date = date(int(year), int(month), int(day))
        if fc_date < date_limit:
            continue
        #understand which ones are the FCs with a single sample per lane
        single_sample_lanes = []
        lanes = {}
        if 'samplesheet_csv' not in flowcell_db[fc_doc]:
            continue
        for sample in flowcell_db[fc_doc]['samplesheet_csv']:
            if sample['Lane'] not in lanes:
                lanes[sample['Lane']] = []
            lanes[sample['Lane']].append(sample['index'])
        for lane in lanes:
            #if only one sample per lane
            if len(lanes[lane]) == 1:
                single_sample_lanes.append([lane, lanes[lane][0]])
        #now I know what are the lanes with a single index
        #now collect stats
        for lane_index in single_sample_lanes:
            lane = lane_index[0]
            index = lane_index[1]
            #get percentage of undetermined
            if lane not in flowcell_db[fc_doc]["Undetermined"]:
                continue #it means this lane has no undetermined
            pc_undet = [sample['% of thelane'] for sample in flowcell_db[fc_doc]['illumina']['Demultiplex_Stats']['Barcode_lane_statistics'] if sample['Lane']==lane and sample['Barcode sequence']=='unknown'][0]
            try:
                pc_undet = float(pc_undet)
            except ValueError: #sometimes it is empty
                continue
            if pc_undet > 10:
                if index not in undet_stats[instrument_name]:
                    undet_stats[instrument_name][index] = 0 #initialiaze this
                    indexes[index] = 0 #mark this as seen
                undet_stats[instrument_name][index] += 1 # seen a lane with high amount of undetermined

    print(",", end=' ')
    for index in indexes:
        print("{},".format(index), end=' ')
    print("")
    for instrument in undet_stats:
        print("{},".format(instrument), end=' ')
        for index in indexes:
            if index in undet_stats[instrument]:
                print("{},".format(undet_stats[instrument][index]), end=' ')
            else:
                print("0,", end=' ')
        print("")
    print("")





def find_undetermined_index_over_time(index_to_be_searched, instrument_type):
    couch=setupServer(CONFIG)
    flowcell_db = couch["x_flowcells"]
    flowcell_docs = {}
    for fc_doc in flowcell_db:
        try:
            undetermined = flowcell_db[fc_doc]["Undetermined"]
        except KeyError:
            continue
        flowcell_docs[flowcell_db[fc_doc]["RunInfo"]["Id"]] = fc_doc


    time_line = []

    for FCid in sorted(flowcell_docs):
        # first check that I have all necessary info to extract information
        fc_doc = flowcell_docs[FCid]
        FC_type = get_FC_type(FCid)
        #if a instrument type is specifed process only FCs run on that instrument
        if instrument_type is not None:
            if instrument_type != FC_type:
                continue
        undetermined = flowcell_db[fc_doc]["Undetermined"]
        lanes_undet = [FCid, []]
        for lane in ['1','2','3','4','5','6','7','8']:
            if lane not in undetermined:
                continue
            index_to_be_searched_count = 0
            for undetermined_index in undetermined[lane]:
                if index_to_be_searched in undetermined_index:
                    index_to_be_searched_count = undetermined[lane][undetermined_index]
            lanes_undet[1].append([lane, index_to_be_searched_count])
        if len(lanes_undet[1]) > 0:
            time_line.append(lanes_undet)

    for FC in time_line:
        FCid = FC[0]
        for lane in FC[1]:
            print("{}_{} {}".format(FCid, lane[0], lane[1]))




def undet_index_to_projects(index_to_be_searched, instrument_type, min_occurences=0):
    status_db = setupServer(CONFIG)
    workset_db = status_db['worksets']
    workset_project_view = workset_db.view('project/ws_proj')


    flowcell_db = status_db["x_flowcells"]
    flowcell_docs = {}
    counter = 0
    projects_with_undet_in_fc_set = set()
    worksets_with_undet_in_fc     = {}
    for fc_doc in flowcell_db:
        try:
            undetermined = flowcell_db[fc_doc]["Undetermined"]
        except KeyError:
            continue
        FCid = flowcell_db[fc_doc]["RunInfo"]["Id"]
        # first check that I have all necessary info to extract information
        FC_type = get_FC_type(FCid)
        #if a instrument type is specifed process only FCs run on that instrument
        if instrument_type is not None:
            if instrument_type != FC_type:
                continue
        undetermined = flowcell_db[fc_doc]["Undetermined"]
        for lane in ['1','2','3','4','5','6','7','8']:
            if lane not in undetermined:
                continue
            index_to_be_searched_count = 0
            if  index_to_be_searched in undetermined[lane] and undetermined[lane][index_to_be_searched] > min_occurences:
                name = 'SampleName'
                for samplesheet_entry in flowcell_db[fc_doc]["samplesheet_csv"]:
                    if 'SampleName' not in samplesheet_entry:
                         name = 'Sample_Name'
                samples_with_undet_in_lane  = set([samplesheet_entry[name] for samplesheet_entry in  flowcell_db[fc_doc]["samplesheet_csv"] if samplesheet_entry['Lane']==lane])
                projects_with_undet_in_lane = set([samplesheet_entry[name].split("_")[0] for samplesheet_entry in  flowcell_db[fc_doc]["samplesheet_csv"] if samplesheet_entry['Lane']==lane])
                projects_with_undet_in_fc_set.update(projects_with_undet_in_lane)
                #find out which workset contains these samples
                for project in projects_with_undet_in_lane:
                    #for each proejct look which workset has been involved
                    #if len(workset_project_view[project].rows) > 1:
                    #    import pdb
                    #    pdb.set_trace()

                    samples_with_undet_ws  = {}
                    for sample in samples_with_undet_in_lane:
                        #now I need to figure out in which WS the samples were... might be more than one as samples might be pooled
                        for row in workset_project_view[project].rows:
                            ws_doc_id = row.id
                            ws_id = list(row.value.keys())[0] #I am pretty sure that for each row I have a sinlge entry
                            if sample in list(row.value[ws_id]['samples'].keys()):
                                location = row.value[ws_id]['samples'][sample]['location']
                                #now I know that this sample in this lane in this FC was affected by index presence and I know the position
                                if ws_id not in worksets_with_undet_in_fc:
                                    worksets_with_undet_in_fc[ws_id] = {}
                                if FCid not in  worksets_with_undet_in_fc[ws_id]:
                                    worksets_with_undet_in_fc[ws_id][FCid] = {}
                                if lane not in worksets_with_undet_in_fc[ws_id][FCid]:
                                    worksets_with_undet_in_fc[ws_id][FCid][lane] = set()
                                worksets_with_undet_in_fc[ws_id][FCid][lane].add((sample,location))

    for ws_id in sorted(worksets_with_undet_in_fc):
        print(ws_id)
        for run_id in sorted(worksets_with_undet_in_fc[ws_id]):
            print("\t{}".format(run_id))
            for lane in sorted(worksets_with_undet_in_fc[ws_id][run_id]):
                sys.stdout.write("\t\t{}: ".format(lane))
                for sample_location in worksets_with_undet_in_fc[ws_id][run_id][lane]:
                    sys.stdout.write("({},{}) ".format(sample_location[0], sample_location[1]))
                sys.stdout.write('\n')






def fetch_undermined_stats():
    #initialise
    couch=setupServer(CONFIG)
    flowcell_db = couch["x_flowcells"]
    #initialise counters for all FCs
    MostOccurringUndetIndexes = {}
    FC_num = 0
    lanes_num = 0
    MostOccurringUndetIndexes["Total"] = {}
    #for HiSeqX FCs
    FC_XTen_num = 0
    lanes_Xten_num = 0
    MostOccurringUndetIndexes["HiSeqX"] = {}
    #for MiSeq FCs
    FC_MiSeq_num = 0
    lanes_MiSeq_num = 0
    MostOccurringUndetIndexes["MiSeq"] = {}
    #for HiSeq2500 FCs
    FC_HiSeq_num = 0
    lanes_HiSeq_num = 0
    MostOccurringUndetIndexes["HiSeq2500"] = {}
    for fc_doc in sorted(flowcell_db):
        # first check that I have all necessary info to extract information
        try:
            undetermined = flowcell_db[fc_doc]["Undetermined"]
        except KeyError:
            continue
        FCid = flowcell_db[fc_doc]["RunInfo"]["Id"]
        FC_type = get_FC_type(FCid)
        FC_num += 1
        if FC_type == "HiSeqX":
            FC_XTen_num += 1
        elif FC_type == "HiSeq2500":
            FC_HiSeq_num += 1
        elif FC_type == "MiSeq":
            FC_MiSeq_num += 1
        #we can use the illumina Demultiplex_Stats Barcode_lane_statistics to fetch info about indexes
        for lane in undetermined:
            #for each lane
            if len(undetermined[lane]) > 1: # if there are elements (there is the NoIndex case)
                if 'TOTAL' in undetermined[lane]:
                    del undetermined[lane]['TOTAL']
                most_occuring_undet = sorted(list(undetermined[lane].items()), key=operator.itemgetter(1), reverse=True)[0]
                lanes_num += 1
                if FC_type == "HiSeqX":
                    lanes_Xten_num += 1
                elif FC_type == "HiSeq2500":
                    lanes_HiSeq_num += 1
                elif FC_type == "MiSeq":
                    lanes_MiSeq_num += 1

                if most_occuring_undet[0] not in MostOccurringUndetIndexes[FC_type]:
                    MostOccurringUndetIndexes[FC_type][most_occuring_undet[0]] = 0
                MostOccurringUndetIndexes[FC_type][most_occuring_undet[0]] += 1
                if most_occuring_undet[0] not in MostOccurringUndetIndexes["Total"]:
                    MostOccurringUndetIndexes["Total"][most_occuring_undet[0]] = 0
                MostOccurringUndetIndexes["Total"][most_occuring_undet[0]] += 1



    print("Flowcells (lanes): {} ({})".format(FC_num, lanes_num))
    print("HiSeqX (lanes): {} ({})".format(FC_XTen_num, lanes_Xten_num))
    print("HiSeq2500 (lanes): {} ({})".format(FC_HiSeq_num, lanes_HiSeq_num))
    print("MiSeq (lanes): {} ({})".format(FC_MiSeq_num, lanes_MiSeq_num))

    print("Most occuring undetermined (seen in #lanes)")
    print("All Flowcells:")
    for twenty_most_occuring_undet in sorted(list(MostOccurringUndetIndexes["Total"].items()), key=operator.itemgetter(1), reverse=True)[0:10]:
        print("{}\t{}\t{}".format(twenty_most_occuring_undet[0], twenty_most_occuring_undet[1], twenty_most_occuring_undet[1]/float(lanes_num)))
    print("All HiSeqX:")
    for twenty_most_occuring_undet in sorted(list(MostOccurringUndetIndexes["HiSeqX"].items()), key=operator.itemgetter(1), reverse=True)[0:10]:
        print("{}\t{}\t{}".format(twenty_most_occuring_undet[0], twenty_most_occuring_undet[1], twenty_most_occuring_undet[1]/float(lanes_Xten_num)))
    print("All HiSeq2500:")
    for twenty_most_occuring_undet in sorted(list(MostOccurringUndetIndexes["HiSeq2500"].items()), key=operator.itemgetter(1), reverse=True)[0:10]:
        print("{}\t{}\t{}".format(twenty_most_occuring_undet[0], twenty_most_occuring_undet[1], twenty_most_occuring_undet[1]/float(lanes_HiSeq_num)))
    print("All MiSeq:")
    for twenty_most_occuring_undet in sorted(list(MostOccurringUndetIndexes["MiSeq"].items()), key=operator.itemgetter(1), reverse=True)[0:10]:
        print("{}\t{}\t{}".format(twenty_most_occuring_undet[0], twenty_most_occuring_undet[1], twenty_most_occuring_undet[1]/float(lanes_MiSeq_num)))




def fetch_pooled_projects(instrument_type):
    status_db = setupServer(CONFIG)
    flowcell_db = status_db["x_flowcells"]
    counter = 0
    projects_pooled = {}
    for fc_doc in flowcell_db:
        if 'RunInfo' not in flowcell_db[fc_doc]:
            continue
        FCid = flowcell_db[fc_doc]["RunInfo"]["Id"]
        # first check that I have all necessary info to extract information
        FC_type = get_FC_type(FCid)
        #if a instrument type is specifed process only FCs run on that instrument
        if instrument_type is not None:
            if instrument_type != FC_type:
                continue
        if 'illumina' not in flowcell_db[fc_doc]:
            print("Not illumina field found in doc")
            continue
        if 'Demultiplex_Stats' not in  flowcell_db[fc_doc]['illumina']:
            print("Not Demultiplex_Stats field found in doc")
            continue
        if 'Barcode_lane_statistics' not in flowcell_db[fc_doc]['illumina']['Demultiplex_Stats']:
            print("Not Barcode_lane_statistics field found in doc")
            continue
        demux_stats = flowcell_db[fc_doc]['illumina']['Demultiplex_Stats']['Barcode_lane_statistics']
        for lane in ['1','2','3','4','5','6','7','8']:
            samples_in_lane =  [entry['Sample'] for entry in demux_stats if entry['Lane'] == lane and not(entry['Sample'] == 'unknown' or entry['Sample'] == 'Undetermined') ]
            if len(samples_in_lane) > 1:
                #it means pooled
                projects = set( entry['Project'] for entry in demux_stats if entry['Lane'] == lane and not(entry['Sample'] == 'unknown' or entry['Sample'] == 'Undetermined') )
                samples_concat= ""
                for sample in sorted(samples_in_lane):
                    samples_concat += sample + " "
                for project in projects:
                    if project not in projects_pooled:
                        projects_pooled[project] = set()
                    projects_pooled[project].add(samples_concat)

    for project in projects_pooled:
        print(project)
        for pool in projects_pooled[project]:
            print("\t{}".format(pool))


def main(args):
    configuration_file = args.config
    load_yaml_config(configuration_file)

    if args.mode == 'most_undet':
        fetch_undermined_stats()

    if args.mode == 'check_undet_index':
        if args.index is None:
            sys.exit("in this mode --index must be specified")
        find_undetermined_index_over_time(args.index, args.instrument_type)

    if args.mode == 'workset_undet':
        if args.index is None:
            sys.exit("in this mode --index must be specified")
        undet_index_to_projects(args.index, args.instrument_type, args.min_occurences)


    if args.mode == 'single_sample_lanes':
        check_single_sample_lanes("HiSeqX")

    if args.mode == 'fetch_pooled_projects':
        fetch_pooled_projects(args.instrument_type)








if __name__ == '__main__':
    parser = argparse.ArgumentParser("""This scripts queries statusdb x_flowcell_db  and fetch informaiton about runs.
    The following operations are supported:
        - check_undet_index: given a specific index checks all FCs and prints all FC and lanes where the indx appears as undetermined
        - most_undet: outputs a summary about undetermiend indexes, printing the most 20 most occurring indexes for each instrument type
        - single_sample_lanes: prints stats about HiSeqX lanes run with a single sample in it
        - workset_undet: prints for each workset the FC, lanes and samples where the specified index has been found in undet. For each sample the plate position is printed.
        """)
    parser.add_argument('--config', help="configuration file", type=str,  required=True)
    parser.add_argument('--indexes', help="yamls file containing indexes we want to analyse", type=str)
    parser.add_argument('--min_occurences', help="minimum number of occurences in undet in workset_undet mode", type=int, default=0)


    parser.add_argument('--mode', help="define what action needs to be executed", type=str, required=True, choices=('check_undet_index', 'most_undet', 'single_sample_lanes', 'workset_undet', 'fetch_pooled_projects'))


    parser.add_argument('--index', help="a specifc index (e.g., CTTGTAAT) to be searched across lanes and FCs", type=str)
    parser.add_argument('--instrument-type', help="type of instrument", type=str, default=None, choices=('HiSeqX', 'MiSeq', 'HiSeq2500'))
    args = parser.parse_args()
    main(args)
