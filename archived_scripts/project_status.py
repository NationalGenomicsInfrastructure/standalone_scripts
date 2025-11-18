import sys, os, glob
import argparse
from operator import itemgetter
import glob
import six

def init_sample_hash_emtry():
    empty_sample_result = {
        '#Archived_runs' : 0,
        '#Data_runs': 0,
        '#Analysis_runs': 0,
        '#Reads':0,
        'RowCov':0,
        '#AlignedReads':0,
        '%AlignedReads':0,
        'AlignCov':0,
        '%Dup':0,
        'MedianInsertSize':0,
        'GCpercentage':0,
        'Delivered':False
    }
    return empty_sample_result

def find_samples_from_archive(roots, project, samples, stockholm):
    """given a project (e.g. P1775 or OB-0726) finds all samples sequenced for that specif project
    it assumes that we never delete the folder stucture, but only fastq files
    returns an hash with one sample name as key and number of seq runs that contain that sample
     """
    for root in roots:
        for dir in os.listdir(root):
            if "_ST-" in dir:
                #must be an X FC
                run_dir     = os.path.join(root, dir)
                sample_dirs = glob.glob("{}/Demultiplexing/*/Sample_*".format(run_dir))
                for sample in sample_dirs:
                    if stockholm:
                        sample_name = sample.split("/")[-1].replace("Sample_", "")
                        if not sample_name.startswith(project):
                            continue
                    else:#uppsala case
                        current_project = sample.split("/")[-2]
                        if project != current_project:
                            continue
                        sample_name = sample.split("/")[-1].replace("Sample_", "")
                    if not sample_name in samples:
                        samples[sample_name] = init_sample_hash_emtry()
                    archived_runs = len(glob.glob("{}/{}*L0*R1*fastq.gz".format(sample,sample_name)))
                    if archived_runs == 0: #stockholm case
                        sampe_name_hyphen = sample_name.replace("_", "-")
                        archived_runs = len(glob.glob("{}/{}*L00*R1*fastq.gz".format(sample,sampe_name_hyphen)))
                    samples[sample_name]["#Archived_runs"] += archived_runs


def find_sample_from_DATA(root, project, samples ):
    """given a project (e.g. P1775) finds all samples tranfered to DATA folder
    returns an hash with one sample name as key and number of seq runs (or lanes runs)
    """
    if not os.path.exists(os.path.join(root,project)):
        return samples

    for sample in os.listdir(os.path.join(root,project)):
        #DATA/SAMPLE/LIB_PREPS/RUNS
        if sample.startswith("."):
            continue
        sample_data_dir = os.path.join(root, project, sample)
        sample_runs     = glob.glob("{}/*/*/{}*L0*_R1*fastq.gz".format(sample_data_dir,sample)) #if sample splitted in multiple lanes there will be an entry per lane
        if not sample in samples:
            samples[sample] = init_sample_hash_emtry()
        samples[sample]['#Data_runs'] = len(sample_runs)


def find_sample_from_ANALYSIS(root, project, samples):
    """given a project (e.g. P1775) finds all samples in ANALYSIS folder
       returns an hash with one sample name as key and various stats on the sample
       It does this by looking at the bam.out files that is present in the 01_raw_alignments folder
       A sample is counted here if it is found in 01_raw_alignments
    """
    raw_alignments_dir = os.path.join(root, project, "piper_ngi", "01_raw_alignments")
    for sample_run in glob.glob("{}/*.out".format(raw_alignments_dir)):
        sample_run_algn = sample_run.split("/")[-1] # this looks like P1775_102.AH2T7GCCXX.P1775_102.1.bam.out
        sample_name = sample_run_algn.split(".")[0]
        sample_lane = int(sample_run_algn.split(".")[3])
        if not sample_name in samples:
            samples[sample_name] = init_sample_hash_emtry()
        samples[sample_name]['#Analysis_runs']  += 1

    # now check if I can retrive other informaiton about  this sample
    for sample, sample_entry in samples.items():
        genome_results_file = os.path.join(root, project, "piper_ngi", "06_final_alignment_qc",
                                           "{}.clean.dedup.qc".format(sample),
                                           "genome_results.txt")

        if os.path.isfile(genome_results_file) and sample_entry['#Analysis_runs'] == 0:
            sample_entry['#Analysis_runs'] = 1 # at least one is present

        if sample_entry['#Analysis_runs'] > 0:
            #if i have run some analysis on this sample fetch info about sequenced reads and coverage
            picard_duplication_metrics = os.path.join(root, project, "piper_ngi", "05_processed_alignments",
                                               "{}.metrics".format(sample))

            if os.path.isfile(genome_results_file):
                #store informations
                parse_qualimap(genome_results_file, sample_entry)


            if os.path.isfile(picard_duplication_metrics) and sample_entry['#Reads'] > 0:
                # if picard file exists and bamqc has been parsed with success
                parse_bamtools_markdup(picard_duplication_metrics, sample_entry)


def find_sample_from_DELIVERY(root, project, samples):
    """given a project (e.g. P1775) finds all samples in DELIVERED folder
       returns an hash with one sample name as key the key delivered set as true or false
    """
    project_delivery_dir = os.path.join(root,project)
    if not os.path.exists(project_delivery_dir):
        return None

    for sample in os.listdir(project_delivery_dir):
        if os.path.isdir(os.path.join(project_delivery_dir, sample)) and sample != "00-Reports":
            if not sample in samples:
                samples[sample] = init_sample_hash_emtry()
            samples[sample]['Delivered'] = True


def parse_bamtools_markdup(picard_duplication_metrics, sample):
    duplication = 0
    with open(picard_duplication_metrics, 'r') as f:
        for line in f:
            line.strip()
            if line.startswith("## METRICS CLASS"):
                line = six.next(f) # this is the header
                line = six.next(f).strip() # thisis the one I am intrested
                duplicate_stats= line.split()
                UNPAIRED_READ_DUPLICATES = int(duplicate_stats[4])
                READ_PAIR_DUPLICATES     = int(duplicate_stats[5])
                PERCENT_DUPLICATION      = float(duplicate_stats[7].replace(",", "."))# some times a comma is used
                sample['%Dup']           = PERCENT_DUPLICATION





def parse_qualimap(genome_results_file, sample):
    reference_size         = 0
    number_of_reads        = 0
    number_of_mapped_reads = 0
    coverage_mapped        = 0
    coverage_raw           = 0
    GCpercentage           = 0
    MedianInsertSize       = 0
    autosomal_cov_length   = 0
    autosomal_cov_bases    = 0

    reference_section = False
    global_section    = False
    coverage_section  = False
    coverage_section  = False
    coverage_per_contig_section = False
    insertSize_section= False
    with open(genome_results_file, 'r') as f:
        for line in f:
            if line.startswith('>>>>>>> Reference'):
                reference_section = True
                continue
            if line.startswith('>>>>>>> Globals'):
                reference_section = False
                global_section    = True
                continue
            if line.startswith('>>>>>>> Insert'):
                global_section    = False
                insertSize_section= True
                continue
            if line.startswith('>>>>>>> Coverage per contig'):
                coverage_section     = False
                coverage_per_contig_section = True
                continue
            if line.startswith('>>>>>>> Coverage'):
                coverage_section   = True
                insertSize_section = False
                continue


            if reference_section:
                line = line.strip()
                if "number of bases" in line:
                    reference_size = int(line.split()[4].replace(",", ""))
                    reference_section = False
            if global_section:
                line = line.strip()
                if "number of reads" in line:
                    number_of_reads = int(line.split()[4].replace(",", ""))
                if "number of mapped reads" in line:
                    number_of_mapped_reads = int(line.split()[5].replace(",", ""))
            if insertSize_section:
                line = line.strip()
                if "median insert size" in line:
                    MedianInsertSize = int(line.split()[4])
            if coverage_section:
                line = line.strip()
                if "mean coverageData" in line:
                    coverage_mapped = float(line.split()[3].replace("X", ""))
            if coverage_per_contig_section:
                line = line.strip()
                if line:
                    sections = line.split()
                    if sections[0].isdigit() and int(sections[0]) <= 22:
                        autosomal_cov_length += float(sections[1])
                        autosomal_cov_bases += float(sections[2])

    sample['#Reads'] = number_of_reads
    sample['RowCov'] = (number_of_reads*150)/float(reference_size)
    sample['#AlignedReads'] = number_of_mapped_reads
    sample['%AlignedReads'] = (float(number_of_mapped_reads)/number_of_reads)*100
    sample['AlignCov'] = coverage_mapped
    sample['MedianInsertSize'] = MedianInsertSize
    sample['AutosomalCoverage'] = autosomal_cov_bases / autosomal_cov_length





def main(args):
    uppmax_id    = args.uppmax_project
    stockholm    = args.stockholm
    raw_data     = "/proj/{}/nobackup/NGI/DATA/".format(uppmax_id)
    analysis_dir = "/proj/{}/nobackup/NGI/ANALYSIS/".format(uppmax_id)
    delivery_dir = "/proj/{}/nobackup/NGI/DELIVERY/".format(uppmax_id)
    archive      = ("/proj/{}/archive/".format(uppmax_id), "/proj/{}/incoming/".format(uppmax_id))
    samples      = {}


    projects     = [item for sublist in args.projects for item in sublist]

    if args.project_status:
        if len(args.projects[0]) != 1:
            print("WARNING: only one project when project-status specified\n")
            return

    for project in args.projects[0]:
        #find all samples sequenced for a project present in archive -- this assumes that fastq files will be deleted but not the folder structure
        find_samples_from_archive(archive, project, samples, stockholm)
        #now find samples that are stored in DATA
        find_sample_from_DATA(raw_data, project, samples)
        find_sample_from_ANALYSIS(analysis_dir, project, samples)
        find_sample_from_DELIVERY(delivery_dir, project, samples)

    if args.project_status:
        sequenced_samples = 0
        delivered_samples = 0
        print("SAMPLE\tARCHIVE_SEQ_RUN\tDATA_SEQ_RUN\tANALYSIS_SEQ_RUN")
        for sample, sample_entry in samples.items():
            sequenced_samples +=1
            if  sample_entry['Delivered']:
                delivered_samples += 1
            print("{}\t{}\t{}\t{}".format(
                sample,
                sample_entry['#Archived_runs'],
                sample_entry['#Data_runs'],
                sample_entry['#Analysis_runs']
            ))
        print("PROJECT SUMMARY:")
        print("  SAMPLES_SEQUENCED: {}".format(sequenced_samples))
        print("  SAMPLES_DELIVERED: {}".format(delivered_samples))


    else:
        for sample, sample_entry in samples.items():
            skip_print = 0;
            if sample_entry['#Archived_runs'] != sample_entry['#Data_runs']:
                skip_print = 0
            if sample_entry['#Archived_runs'] != sample_entry['#Analysis_runs']:
                skip_print = 0
            if sample_entry['#Analysis_runs'] == 0:
                skip_print = 1 # no problem here as might have demux runs

            if skip_print == 1:
                print("WARNING: Sample {} has incoherent numbers of runs: ({} {} {})".format(sample,
                                                sample_entry['#Archived_runs'],
                                                sample_entry['#Data_runs'],
                                                sample_entry['#Analysis_runs']
                                                ))
            samples[sample]["skip print"] = skip_print

        if not args.skip_header:
            print("sample_name\t#Reads\tRaw_coverage\t#Aligned_reads\t%Aligned_reads\tAlign_cov\tAutosomalCoverage\t%Dup\tMedianInsertSize")

        for sample, sample_entry in samples.items():
            if sample_entry["skip print"] == 0:
                print("{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}".format(
                    sample,
                    sample_entry['#Reads'],
                    sample_entry['RowCov'],
                    sample_entry['#AlignedReads'],
                    sample_entry['%AlignedReads'],
                    sample_entry['AlignCov'],
                    sample_entry['AutosomalCoverage'],
                    sample_entry['%Dup'],
                    sample_entry['MedianInsertSize']
                ))




if __name__ == '__main__':
    parser = argparse.ArgumentParser("""Process one or more project and report basic statistiscs for it """)
    parser.add_argument('--projects', help="Projects we want to have statistics for, in stockholm case P1000, uppsala NK-0191", type=str,   action='append', nargs='+')
    parser.add_argument('--uppmax-project', help="uppmax project where analysis have been run", type=str, required=True)
    parser.add_argument('--project-status', help="reports number of samples, of samples-runs, analysed samples and delivered samples (work only if a single project is specified)", action='store_true')

    parser.add_argument('--skip-header', help="skip header", action='store_true')
    parser.add_argument('--stockholm', help="assume stocholm project format, otherwise uppsala", action='store_true', default=True)

    args = parser.parse_args()

    if not args.projects:
        print("ERROR: projects must be specified")
        sys.exit()

    main(args)
