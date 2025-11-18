#!/usr/bin/env python2.7

import re
import math
import unicodedata
import csv
import copy
import sys
import os
import yaml

from ibmcloudant import CouchDbSessionAuthenticator, cloudant_v1
import numpy
import click

from collections import defaultdict, Counter, OrderedDict
from time import time
from datetime import datetime

from genologics.config import BASEURI, USERNAME, PASSWORD
from genologics.lims import Lims
from genologics.entities import Process

def credentials():
    try:
        config_file = os.path.join(os.environ.get("HOME"), ".ngi_config", "statusdb.yaml")
        if not os.path.exists(config_file):
            config_file = os.path.join(os.environ.get("REPOOLER_CONFIG"))
        with open(config_file) as f:
            conf = yaml.load(f)
            config = conf["statusdb"]
        return config
    except IOError:
        raise IOError(("There was a problem loading the configuration file. "
                "Please make sure that ~/.ngi_config/statusdb.yaml exists "
                "or env vairable 'REPOOLER_CONFIG' is set with path to conf "
                "file and set with read permissions"))

#Assumes ind. sample conc measurements have failed. As such it relies on changing relative volume on already normalized samples and structure
#Structure are retained as conc measurements failure means there's no way to know conc. delta between samples from seperate poolss
def connection():
    config = credentials()
    user = config.get("username")
    pw = config.get("password")
    print("Database server used: https://{}".format(config.get("url")))
    print("LIMS server used: " + BASEURI)
    couch = cloudant_v1.CloudantV1(
        authenticator=CouchDbSessionAuthenticator(
            user,
            pw
        )
    )
    couch.set_service_url(f"https://{config.get('url')}")

    try:
        print("Connecting to statusDB...")
        couch.get_server_information().get_result()
    except:
        sys.exit("Can't connect to couch server. Username & Password is incorrect, or network is inaccessible.")
    print("Connected!")
    return couch


def proj_struct(couch, project, target_clusters):
    """"Fetches the structure of a project"""
    db = "x_flowcells"
    view = couch.post_view(
        db=db,
        ddoc='names',
        view='project_ids_list'
    ).get_result()
    fc_track = defaultdict(set)

    #Adds flowcells to ALL projects. Due to interactions its easier to just get FCs for ALL projects
    for rec in view["rows"]:
        fc = ''.join(rec["key"])
        fc = unicodedata.normalize('NFKD', fc).encode('ascii','ignore')
        id = ''.join(rec["id"])
        id = unicodedata.normalize('NFKD', id).encode('ascii','ignore')
        for projs in rec["value"]:
            projs = ''.join(projs)
            projs = unicodedata.normalize('NFKD', projs).encode('ascii','ignore')
            if fc_track[projs] == set([]):
                fc_track[projs] = dict()
            fc_track[projs][fc] = id

    #Adds lanes and samples to flowcells, includes samples from other projects if they share lane
    if fc_track[project] == set([]):
        raise Exception('Error: Project not logged in x_flowcells database!')
    for fc, id in fc_track[project].items():
        try:
            entry_illumina = couch.get_document(db=db, document_id=id).get_result()['illumina']
        except KeyError:
            print("Error: Illumina table for db entry" , id, "doesn't exist!")
        entry = entry_illumina['Demultiplex_Stats']['Barcode_lane_statistics']
        for index in range(0, len(entry)):
            lane = entry[index]['Lane']
            sample = entry[index]['Sample']
            if 'Clusters' in entry[index]:
                clusters = entry[index]['Clusters']
            else:
                clusters = entry[index]['PF Clusters']
            clusters = int(re.sub(r",", "", clusters))


            if not isinstance(fc_track[project][fc], dict):
                fc_track[project][fc] = dict()
            if not lane in fc_track[project][fc]:
                fc_track[project][fc][lane] = dict()
            #Only counts samples for the given project, other samples are "auto-filled"
            if project in sample or sample in "Undetermined":
                fc_track[project][fc][lane][sample] = clusters
            else:
                fc_track[project][fc][lane][sample] = target_clusters
    #Removes any lanes that don't have any part project samples
    for fc, lanes in fc_track[project].items():
        for lane,sample in lanes.items():
            if not any(project in s for s in list(sample.keys())):
                   del fc_track[project][fc][lane]
    return fc_track[project]


def parse_indata(struct, target_clusters):
    """Takes in data and finds unique lane structure, clusters per sample and lane division"""
    clusters_rem = dict() #Total remaining clusters
    lane_maps = dict() #All possible structs. Sample-aggregate is a string, values are average expression
    sample_struct = dict() #All possible structs. Values are a list of samples per lane
    copies = dict() #Keeps track of duplicates of each structure

    for fc, lanes in struct.items():
        for lane, samples in lanes.items():
            #Concats the structure into a set of unique structures
            mapp = sorted(samples, reverse=True)
            map_names = str(mapp)
            map_values = [value for (key, value) in sorted(samples.items(), reverse=True)]

            if not map_names in lane_maps:
                sample_struct[map_names] = mapp
                lane_maps[map_names] = numpy.array(map_values)
                copies[map_names] = 1
            else:
                lane_maps[map_names] = lane_maps[map_names] + numpy.array(map_values)
                copies[map_names] = copies[map_names]+1

            #Calculate remaining clusters for each sample
            for sample, value in samples.items():
                if not sample in clusters_rem:
                    clusters_rem[sample] = target_clusters
                clusters_rem[sample] -= value

                if clusters_rem[sample] < 0:
                    clusters_rem[sample] = 0

    #Calculate average output of sample for each structure type
    for names, values in lane_maps.items():
        values = numpy.array(values)
        #Explicit typecast
        values = values.astype(float)
        #Calculate average
        lane_maps[names] = values/copies[names]
        #Manually overwrites undetermined to exclude them
        #lane_maps[names][0] = 0

    return [lane_maps, clusters_rem, sample_struct]


def simple_unique_set(sample_struct, clusters_rem, target_clusters):
    """Creates a set where every sample uniquely appears once and only once
    Prioritizes lanes with most unsequenced samples first"""

    #Inline function to calculate number of unfinished samples per lane
    def f(x):
        count = 0
        for sample in x:
            if clusters_rem[sample] < target_clusters:
                count = count + 1
        #print count
        return count

    #Best = Most samples in need of sequencing = End of lists
    top_lanes = OrderedDict(sorted(sample_struct.items(), key=lambda t: f(t[1])))
    confirmed_best = dict()
    while not list(top_lanes.keys()) == []:
        #pop the best candidate
        prime = top_lanes.popitem()
        #Check that including the prime doesn't induce duplicates
        sample_ok = True
        for p_sample in prime[1]:
            for keys, values in confirmed_best.items():
                if p_sample in values and p_sample != "Undetermined":
                    #print("DEBUG: {} is already present in final set, auto-discarding {}".format(p_sample, keys))
                    sample_ok = False
                    break
        #Part 2
        if sample_ok:
            #Check that including prime doesn't block a remaining lane with uniques.
            #Tally up blocked lanes & samples from them
            blocked_lanes = list()
            blocked_samples = list()
            for p_sample in prime[1]:
                for t_key, t_sample in top_lanes.items():
                    if p_sample in t_sample and p_sample != "Undetermined":
                        blocked_lanes.append(t_key)
                        blocked_samples.extend(top_lanes[t_key])
            #Break if all instances of a sample end up in blocked lanes
            #Must handle case where no blocked samples happen
            sample_killed = False
            for b_sample in set(blocked_samples):
                sample_killed = True
                for r_key, r_values in top_lanes.items():
                    if (b_sample in r_values or b_sample in prime[1]) and r_key not in blocked_lanes:
                        sample_killed = False
                        break
            if not sample_killed:
                confirmed_best[prime[0]] = prime[1]
                #DEBUG: Remove blocked lanes from top_lanes
                for b_lane in set(blocked_lanes):
                    #print ("DEBUG: {} was blocked by another sample's inclusion!".format(b_lane))
                    del top_lanes[b_lane]

    validate_samples_unique(confirmed_best)
    validate_all_samples_present(confirmed_best, sample_struct, clusters_rem)

    return confirmed_best

def validate_samples_unique(lane_maps):
    """Crude way to check that no samples are in different TYPES of lanes"""
    tempList = list()

    #Put all values in a list
    for k, v in lane_maps.items():
        for index in range(1,len(v)):
            if not v[index] == 'Undetermined':
                tempList.append(v[index])

    #Count instances of all items in the list
    counter = Counter(tempList)
    for values in counter.values():
        if values > 1:
            raise Exception('Error: Sample present in multiple structures. Unhandled exception!')

def validate_all_samples_present(subset_struct, sample_struct, clusters_rem):
    """Takes sample_struct and a subset of sample_struct, and checks that all present in sample_struct with remaining clusters
    also exist in subset_struct"""
    #Form a list of all samples in sample_struct
    tempList = list()
    for key, samplelist in sample_struct.items():
        for element in samplelist:
            if not element in tempList and clusters_rem[element] > 0:
                tempList.append(element)

    #Verify that all samples exist in subset_struct
    for element in tempList:
        found = False
        for key, samplelist in subset_struct.items():
            if element in samplelist:
                found = True
                break
        if not found:
            raise Exception('Error: Sample missing after subset was generated! {} is one of these'.format(element))

def sample_distributor(sample_struct, clusters_rem, clusters_per_lane):
    """Gives the percentage volume each sample should have in a lane, BEFORE accounting
    for concentration offsets"""
    desired_ratios = dict()
    # Key: string of samples. values: Percentage, sample order as sample_struct.values()
    ideal_lanes = dict()
    needed_lanes = dict()

    for s_key, s_val in sample_struct.items():
        #Calculate lane total (sum)
        lane_total = 0
        corrected_total = 0
        for entry in sample_struct[s_key]:
            if clusters_rem[entry] > 0:
                lane_total += clusters_rem[entry]
                if entry != "Undetermined":
                    corrected_total += clusters_rem[entry]

        #Ignores Undetermined in clusters remaining. Idealy this would be ignored everywher
        #but the calculations are complex.
        ideal_lanes[s_key] = corrected_total/float(clusters_per_lane)
        needed_lanes[s_key] = math.ceil(ideal_lanes[s_key])

        #Populate output sample rates
        desired_ratios[s_key] = []
        for entry in sample_struct[s_key]:
            if lane_total == 0:
                desired_ratios[s_key].append(0)
            elif clusters_rem[entry] > 0:
                desired_ratios[s_key].append(clusters_rem[entry]/float(lane_total))
            else:
                desired_ratios[s_key].append(0/float(lane_total))

    #desired ratios = desired clusters per lane (for given sample) / clusters per lane
    return [desired_ratios, needed_lanes, ideal_lanes]

def integrate_conc_diff(lane_maps, desired_ratios):
    """Since some samples are strong and some weaksauce
    10% in desired_ratios does not mean 10% of lane volume
    Ignores undetermined clusters in calculation
    Sample conc assumably cant be altered; aka only volume is modified"""

    volume_ratios = dict()
    conc_factor = dict()

    #FROM HERE NEEDS HEAVY REVISION
    for key in desired_ratios:
        #Assumes no samples had unequal volume in structure
        #TODO: Use LIMS integration to avoid this assumption
        actual_output = lane_maps[key]/sum(lane_maps[key])

        expect_output = []
        expect_output.append(0.0) #Expect 0 undetermined
        for index in range(1, len(lane_maps[key])):
            expect_output.append(1/float(len(lane_maps[key]) -1))#-1 Removes undetermined
        #TODO: One could include undetermined here
        #Overriding errstate since expected is 0 for undetermined
        with numpy.errstate(divide='ignore'):
            conc_factor[key] = actual_output/expect_output
        #Division verified. One with high Actual/Expressed should naturally be less of in repool.

        volume_ratios[key] = desired_ratios[key] / conc_factor[key]
        #Merge down to 100%. Makes sense, since samples differ in volume.
        if not sum(volume_ratios[key]) == 0:
            volume_ratios[key] = volume_ratios[key]/sum(volume_ratios[key])
        else:
            # If lane struct 'is done', force array to contain a list of zeros
            volume_ratios[key] = numpy.zeros(len(volume_ratios[key]))
    return volume_ratios, conc_factor

def realize_numbers(lane_maps, best_sample_struct, volume_ratios, conc_factor, clusters_rem, total_lanes, pool_excess, lane_volume, min_pipette):
    """Actual numbers need to be offset to:
    Work with a pipette minimum and pipette threshold in relation to pool size (5 ul) + excess.
    Lanesum is then downsized to sub 100% with as equal coverage as possible.
    If impossible entire process is rerun with an extra lane for that struct.
    """
    extra_lanes=dict()
    rounded_ratios = dict()
    final_pool_sizes = dict()

    #Remaining clusters for structure
    rem_list = dict()
    for key, values in best_sample_struct.items():
        t_list = []
        for name in best_sample_struct[key]:
            t_list.append(clusters_rem[name])
        rem_list[key] = numpy.array(t_list)

    #For each structure
    for key, values in volume_ratios.items():
        calculations_done = False
        while not calculations_done:
            poolsize = lane_volume*total_lanes[key] + pool_excess
            minTres = round(min_pipette/poolsize, 6)
            minAdd = round(0.1/poolsize, 6)

            #Rounds all values up
            uprounded = []
            for sample in values:
                #Handles problems with Undetermined
                if sample == 0:
                    uprounded.append(0.0)
                elif sample <= minTres:
                    uprounded.append(minTres)
                else:
                    sample = sample-(sample%minAdd)
                    uprounded.append(sample)
            uprounded = numpy.array(uprounded)

            #Sets 'Undetermined's factor to 0, helps out later.
            conc_factor[key][0] = 0
            calculations_done = True
            while sum(uprounded) > 1.0:
                #Overexpression per sample (expressed - remaining). Expressed = ratio * clusters_per_lane * lanes
                #Uprounded*conc_factor[key] SHOULD even out itself; since it only offsets ratios. Sum still 1
                oe_list = (uprounded*conc_factor[key])*sum(lane_maps[key])*total_lanes[key] - rem_list[key]
                #Order indexes in oe_list by value (descending)
                max_index = oe_list.argsort()[::-1]

                stuck = True
                for most_oe in max_index:
                    #Remove one minAdd IF greater than minTres+minAdd AND (ratio-minAdd)*factor > remaining_clusters
                    if uprounded[most_oe] >= minAdd + minTres and (uprounded[most_oe] - minAdd)*conc_factor[key][most_oe]*sum(lane_maps[key])*total_lanes[key] > rem_list[key][most_oe]:
                        uprounded[most_oe] = uprounded[most_oe] - minAdd
                        stuck = False
                        break
                #Add extra lane, restart the loop for the whole structure (new pool size)
                if stuck:
                    total_lanes[key] = total_lanes[key] + 1
                    if not key in extra_lanes:
                        extra_lanes[key] = 0
                    extra_lanes[key] = extra_lanes[key] + 1
                    calculations_done = False
                    break

        final_pool_sizes[key] = poolsize*float(sum(uprounded))
        ##TODO: Sum of output rounded_ratios is less than 100% before normalizing; weird but likely correct.
        if sum(uprounded) == 0:
            rounded_ratios[key] = uprounded
        else:
            rounded_ratios[key] = uprounded/float(sum(uprounded))
    return [rounded_ratios, final_pool_sizes, extra_lanes]


def generate_output(project_id, dest_plate_list, best_sample_struct,total_lanes, req_lanes, lane_maps, rounded_ratios,
                    target_clusters, clusters_per_lane, extra_lanes, lane_volume, pool_excess, final_pool_sizes, volume_ratios, desired_ratios):
    """"Gathers the container id and well name for all samples in project"""
    timestamp = datetime.fromtimestamp(time()).strftime('%Y-%m-%d_%H:%M')

    #Cred to Denis for providing a base epp
    location = dict()
    lims = Lims(BASEURI, USERNAME, PASSWORD)
    allProjects = lims.get_projects()
    for proj in allProjects:
        if proj.id == project_id:
            projName = proj.name
            break

    #Sets up source id
    #All normalization processes for project
    norms=['Library Normalization (MiSeq) 4.0', 'Library Normalization (Illumina SBS) 4.0','Library Normalization (HiSeq X) 1.0']
    pros=lims.get_processes(type=norms, projectname=projName)
    #For all processes
    for p in pros:
        #For all artifacts in process
        for o in p.all_outputs():
            #If artifact is analyte type and has project name in sample
            if o.type=="Analyte" and project_id in o.name:
                location[o.name.split()[0]] = list()
                location[o.name.split()[0]].append(o.location[0].id)
                location[o.name.split()[0]].append(o.location[1])

    #Continue coding from here
    generate_summary(projName, best_sample_struct, timestamp, project_id, dest_plate_list, total_lanes, req_lanes,
                     lane_maps, rounded_ratios, target_clusters, clusters_per_lane, extra_lanes, volume_ratios, desired_ratios, lane_volume, pool_excess)
    generate_csv(projName, timestamp, location, dest_plate_list, total_lanes, best_sample_struct, rounded_ratios, lane_volume, pool_excess, final_pool_sizes)
    generate_dumpfile(projName, timestamp, location, dest_plate_list, total_lanes, best_sample_struct, rounded_ratios, lane_volume, pool_excess, final_pool_sizes)

def generate_summary(projName, best_sample_struct, timestamp, project_id, dest_plate_list, total_lanes, req_lanes, lane_maps, rounded_ratios,
                     target_clusters, clusters_per_lane, extra_lanes, volume_ratios, desired_ratios, lane_volume, pool_excess):
    """Print stats including duplicates"""

    sumName = '{}_summary_{}.txt'.format(projName, timestamp)
    with open(sumName, "w") as summary:
        if sum(req_lanes.values()) != 0:
            OPT = sum(total_lanes.values())/sum(req_lanes.values())
        else:
            raise Exception('Ideally zero more lanes are required. Unable to calcuate OPT!')

        if OPT > 1.5:
            print("\nWarning, OPT exceeds 1.5x! Generated solution is so poor a better one can likely be done by hand!")

        nonzero_pool_types = 0
        for key, value in total_lanes.items():
            if value > 0:
                nonzero_pool_types = nonzero_pool_types +1

        output = 'Target clusters per sample: {}, Expected clusters per lane: {}\n'.format(str(target_clusters), str(clusters_per_lane))
        output = output + 'Lane volume: {} microliter(s), Pool excess: {} microliter(s)\n'.format(lane_volume, pool_excess)
        output = output + 'Project ID: {}, Destination plate name list: {}\n'.format(project_id, str(dest_plate_list))
        output = (output + 'Ideal lanes (same schema): {}, Total lanes: {}, Expression over theoretical ideal (OPT): {}x\n'
                  .format(str(round(sum(req_lanes.values()),3)), str(sum(total_lanes.values())), str(round(OPT,3))))
        output = output + 'Lanes added due to pipette limitations: {} (this can be mitigated with bigger pools).\n'.format(sum(extra_lanes.values()))
        output = output + 'Unique pools: {}, Average pool duplication (ignores empty): {}\n'.format(str(len(list(total_lanes.keys()))),
                                                                                                    str(round(sum(total_lanes.values())/float(nonzero_pool_types),3)))
        summary.write( output )

        bin = 0
        for key, value in best_sample_struct.items():
            if total_lanes[key] > 0:
                bin  += 1
                if key in extra_lanes:
                    output = '\nLane {:>2} to {:>2}+{}: {:>12} {:>12} {:>14} {:>7}\n'.format(str(bin), str(bin+int(total_lanes[key])-1),str(extra_lanes[key]),
                                                                                      'Corrected Pipette', 'Minimum % (partial lanes)', 'Corrected Volume','Naive ratio')
                    bin += int(total_lanes[key]-1+extra_lanes[key])
                else:
                    output = '\nLane {:>2} to {:>2}: {:>19} {:>12} {:>14} {:>7}\n'.format(str(bin), str(bin+int(total_lanes[key])-1),
                                                                                          'Corrected Pipette','Minimum % (partial lanes)','Corrected Volume','Naive ratio')
                    bin += int(total_lanes[key]-1)
                summary.write( output )

                for sample in range(1, len(value)):
                    if sample != "Undetermined":
                        if key in extra_lanes:
                            lane_ratio = req_lanes[key]/(total_lanes[key]+extra_lanes[key])
                        else:
                            lane_ratio = req_lanes[key]/(total_lanes[key])
                        output = '{:11}{:>22}%{:>25}%{:>16}%{:>11}%\n'.format(str(best_sample_struct[key][sample]), str(round(rounded_ratios[key][sample]*100,2)),
                                                           str(round(volume_ratios[key][sample]*100*lane_ratio,2)),
                                                           str(round(volume_ratios[key][sample]*100,2)),str(round(desired_ratios[key][sample]*100,2)))
                        summary.write( output )

def generate_csv(projName, timestamp, location, dest_plate_list, total_lanes, best_sample_struct, rounded_ratios, lane_volume, pool_excess, final_pool_sizes):
    """Creates the output csv file"""

    name = '{}_repool_{}.csv'.format(projName, timestamp)
    wells = ['Empty','A','B','C','D','E','F','G','H']
    #Index 0 is number, index 1 is Letter
    wellIndex = [1, 1]
    destNo = 0
    pool_max = 200

    with open(name, 'w') as csvfile:
        writer = csv.writer(csvfile)
        for key, value in best_sample_struct.items():
            #If a structure is unused, don't include it in the csv
            if not final_pool_sizes[key] == 0:
                try:
                    dest_plate_list[destNo]
                except IndexError:
                    dest_plate_list.append ('dp_{}'.format(str(destNo+1)))
                    #print "Critical error; not enough destination plates provided"

                if pool_max < final_pool_sizes[key]:
                    raise Exception("Error: A pool has been requested that can't be fit into a single well!")

                for instance in range(1, len(value)):
                    #<source plate ID>,<source well>,<volume>,<destination plate ID>,<destination well>
                    sample = best_sample_struct[key][instance]
                    position = '{}:{}'.format(wells[wellIndex[1]], str(wellIndex[0]))
                    try:
                        out_pool = round(rounded_ratios[key][instance]*final_pool_sizes[key],2)
                        output = location[sample][0],location[sample][1],str(out_pool),str(dest_plate_list[destNo]),position
                    except KeyError:
                        print("Error: Samples incorrectly parsed into database, thus causing sample name conflicts!")
                    if not rounded_ratios[key][instance] == 0:
                        writer.writerow(output)
                #Increment wellsindex
                if not rounded_ratios[key][instance] == 0:
                    if not wellIndex[1] >= 8:
                        wellIndex[1] += 1
                    else:
                        wellIndex[1] = 1
                        if not wellIndex[0] >= 12:
                            wellIndex[0] += 1
                        else:
                            wellIndex[0] = 1
                            destNo += 1
def generate_dumpfile(projName, timestamp, location, dest_plate_list, total_lanes, best_sample_struct, rounded_ratios, lane_volume, pool_excess, final_pool_sizes):
    """Dumps output to a crappy csvfile. Because I'm awesome like that"""

    name = '{}_dumpfile_{}.csv'.format(projName, timestamp)
    wells = ['Empty','A','B','C','D','E','F','G','H']
    #Index 0 is number, index 1 is Letter
    wellIndex = [1, 1]
    destNo = 0
    pool_max = 200

    with open(name, 'w') as csvfile:
        writer = csv.writer(csvfile)
        headers = "<samplename>","<source plate ID>","<source well>","<volume>","<destination plate ID>","<destination well>"
        writer.writerow(headers)
        for key, value in best_sample_struct.items():
            #If a structure is unused, don't include it in the csv
            if not final_pool_sizes[key] == 0:
                try:
                    dest_plate_list[destNo]
                except IndexError:
                    dest_plate_list.append ('dp_{}'.format(str(destNo+1)))
                    #print "Critical error; not enough destination plates provided"

                if pool_max < final_pool_sizes[key]:
                    raise Exception("Error: A pool has been requested that can't be fit into a single well!")

                for instance in range(1, len(value)):
                    #samplename,<source plate ID>,<source well>,<volume>,<destination plate ID>,<destination well>
                    sample = best_sample_struct[key][instance]
                    position = '{}:{}'.format(wells[wellIndex[1]], str(wellIndex[0]))
                    try:
                        out_pool = round(rounded_ratios[key][instance]*final_pool_sizes[key],2)
                        output = sample,location[sample][0],location[sample][1],str(out_pool),str(dest_plate_list[destNo]),position
                    except KeyError:
                        print("Error: Samples incorrectly parsed into database, thus causing sample name conflicts!")
                    if not rounded_ratios[key][instance] == 0:
                        writer.writerow(output)
                #Increment wellsindex
                if not rounded_ratios[key][instance] == 0:
                    if not wellIndex[1] >= 8:
                        wellIndex[1] += 1
                    else:
                        wellIndex[1] = 1
                        if not wellIndex[0] >= 12:
                            wellIndex[0] += 1
                        else:
                            wellIndex[0] = 1
                            destNo += 1

@click.command()
@click.option('--project_id', required=True,help='ID of project to repool. \nExamples: P2652, P1312 etc.')
@click.option('--dest_plate_list', default=['dp_1'],
              help='List of destination plates for the robot\'s csv file. Include too many rather than too few; excess will be unused. Default: [dp_1]')
@click.option('--target_clusters', default=320*1000000, help='Threshold of clusters per sample. \nDefault:320*1000000')
@click.option('--clusters_per_lane', default=380*1000000, help='Expected clusters generated by a single lane/well. \nDefault:380*1000000')
@click.option('--lane_volume', default=5, help='Lane volume. \nDefault:5 (uL)')
@click.option('--pool_excess', default=2, help='Excess pool volume when creating a pool. \nDefault:2 (uL)')
@click.option('--min_pipette', default=1, help='Minimum pipette volume. \nDefault:1 (uL)')

def main(target_clusters, clusters_per_lane, project_id, dest_plate_list, lane_volume, pool_excess, min_pipette):
    """Application that calculates samples under threshold for a project, then calculate the optimal composition for reaching the threshold
    without altering concentrations nor the structure of the pools. Outputs both a summary as well as a functional csv file."""
    print("\nWARNING: Output from repooler is experimental. Remember to review all numbers before re-sequencing.\n")

    couch = connection()
    structure = proj_struct(couch, project_id, target_clusters)
    [lane_maps, clusters_rem, sample_struct] = parse_indata(structure, target_clusters)
    best_sample_struct = simple_unique_set(sample_struct, clusters_rem, target_clusters)
    [desired_ratios, total_lanes, req_lanes] = sample_distributor(best_sample_struct, clusters_rem, clusters_per_lane)
    [volume_ratios, conc_factor] = integrate_conc_diff(lane_maps, desired_ratios)
    [rounded_ratios, final_pool_sizes, extra_lanes] = realize_numbers(lane_maps, best_sample_struct, volume_ratios, conc_factor,
                                                                      clusters_rem, total_lanes, pool_excess, lane_volume, min_pipette)

    generate_output(project_id, dest_plate_list, best_sample_struct, total_lanes, req_lanes, lane_maps, rounded_ratios,
                    target_clusters, clusters_per_lane, extra_lanes,lane_volume,pool_excess, final_pool_sizes, volume_ratios, desired_ratios)
if __name__ == '__main__':
    main()
