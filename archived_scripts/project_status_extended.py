import sys, os, glob
import argparse
from operator import itemgetter
import subprocess
import six


uppmax_id = 'ngi2016003'

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

def find_samples_from_archive(roots, project, samples, stockholm=True):
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

def find_results_from_francesco(uppmax_project, project):
    raw_data_dir = "/proj/{}/nobackup/NGI/DATA/".format(uppmax_project)
    analysis_dir = "/proj/{}/nobackup/NGI/ANALYSIS/".format(uppmax_project)
    delivery_dir = "/proj/{}/nobackup/NGI/DELIVERY/".format(uppmax_project)
    archive_dir  = ("/proj/{}/archive/".format(uppmax_project), "/proj/{}/incoming/".format(uppmax_project))
    samples      = {}

    find_samples_from_archive(archive_dir, project, samples)
    find_sample_from_DATA(raw_data_dir, project, samples)
    find_sample_from_ANALYSIS(analysis_dir, project, samples)
    find_sample_from_DELIVERY(delivery_dir, project, samples)

    return samples

def get_low_coverage(project, results_francesco):
    result = {}

    for sample, sample_data in results_francesco.items():
        coverage = sample_data.get('AutosomalCoverage')
        try:
            coverage = float(coverage)
            if coverage < 28.5:
                result[sample] = coverage
        except:
            # not printing error message
            # if coverage is a string something like '29,1111' - comma will fail
            # and we will never figure out what happens
            result[sample] = coverage
    return result

def get_samples_with_undetermined(data_dir, project):
    """ get all fastq_files from DATA directory
        check which ones named 'Undetermined'
        then add sample and flowcell to the list
    """
    result = {}
    # get list of fastq_files in DATA directory
    project_path = os.path.join(data_dir, project, '*/*/*/*.fastq*')
    fastq_files = glob.glob(project_path)
    for file_path in fastq_files:
        # check which files are named 'Undetermined'
        filename = os.path.basename(file_path)
        if 'Undetermined' in filename:
            # get sample and flowcell id
            sample = filename.split('_Undetermined')[0]
            flowcell = file_path.split('/')[-2]
            # update result list
            if sample not in result:
                result[sample] = [flowcell]
            elif flowcell not in result[sample]:
                result[sample].append(flowcell)
    return result

def get_samples_under_analysis(project):
    result = []
    command = "jobinfo | grep piper_{}".format(project)
    try:
        p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = p.communicate()
    except Exception as e:
        print("Cannot execute command: {}".format(command))
        raise e

    output = output[0].split('\n')
    for line in output:
        # skip empty lines
        if line.strip() != '':
            sample = line.split('piper_{}-'.format(project))[-1].split('-')[0]
            if sample not in result:
                result.append(sample)
    return result

def get_samples_under_qc(project):
    result = []
    command = "jobinfo | grep qc_{}".format(project)
    try:
        p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = p.communicate()
    except Exception as e:
        print("Cannot execute command {}".format(command))
        raise e
    output = output[0].split('\n')
    for line in output:
        # skip empty lines
        if line.strip() != '':
            sample = line.split('qc_{}-'.format(project))[-1]
            if sample not in result:
                result.append(sample)
    return result

def get_samples_with_failed_analysis(project, analysis_dir):
    log_path = os.path.join(analysis_dir, '{}/piper_ngi/logs/{}-*.exit'.format(project, project))
    under_analysis = get_samples_under_analysis(project)
    exit_files = glob.glob(log_path)
    result = {}
    for path in exit_files:
        with open(path, 'r') as exit_file:
            exit_code = exit_file.read().strip()
            # P4603-P4603_189-merge_process_variantcall.exit
            sample = os.path.basename(path).replace('{}-'.format(project), '').split('-')[0]
            if exit_code == '' and sample not in under_analysis:
                if sample in result:
                    result[sample] = [result[sample]]
                    result[sample].append('Empty exit code, but sample is not under analysis')
                else:
                    result[sample] = 'Empty exit code, but sample is not under analysis'
            elif exit_code.strip() != '0' and exit_code != '':
                if sample in result:
                    result[sample] = [result[sample]]
                    result[sample].append('Exit code: {}'.format(exit_code))
                else:
                    result[sample] = 'Exit code: {}'.format(exit_code)
    return result

def get_incoherent_samples(results_francesco):
    result = {}
    for sample_id, sample in results_francesco.items():
        sequenced = sample.get('#Archived_runs', '')
        organized = sample.get('#Data_runs', '')
        analyzed = sample.get('#Analysis_runs', '')
        try:
            sequenced = int(sequenced)
            organized = int(organized)
            analyzed = int(analyzed)
        # if not int or something strange in the results, print it too (just in case)
        except ValueError as e:
            result[sample_id] = {'sequenced': sequenced, 'organized': organized, 'analyzed': analyzed}
        else:
            if not(sequenced == organized == analyzed):
                result[sample_id] = {'sequenced': sequenced, 'organized': organized, 'analyzed': analyzed}
    return result


def get_sequenced(project):
    incoming = "/proj/ngi2016003/incoming"
    project_flowcells = {}
    for fc in os.listdir(incoming):
        sample_sheet = os.path.join(incoming, fc, 'SampleSheet.csv')
        command = 'grep {} {}'.format(project, sample_sheet)
        try:
            p = subprocess.Popen(command, shell=True, universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            print('Command failed: {}'.format(command))
            raise e
        output = p.communicate()[0]
        if output:
            for line in output.split('\n'):
                # if not an empty line
                if line:
                    try:
                        sample = line.split(',')[2]
                    except Exception as e:
                        print(line)
                        print('Skipping line: {} from sample sheet: {}'.format(line, sample_sheet))
                        # if something went wrong
                        continue
                    else:
                        if sample not in project_flowcells:
                            project_flowcells[sample] = [fc]
                        else:
                            project_flowcells[sample].append(fc)
    return project_flowcells

def get_organized(project):
    sequenced = get_sequenced(project)
    project_path = os.path.join('/proj/ngi2016003/nobackup/NGI/DATA', project)
    organized = {}
    for sample in sequenced:
        for fc in sequenced[sample]:
            # '*'' is libprep, can be 'A', 'B', etc
            path = os.path.join(project_path, sample, '*', fc)
            if glob.glob(path): # list of files
                if sample not in organized:
                    organized[sample] = [fc]
                elif fc not in organized[sample]:
                    organized[sample].append(fc)
                # else: skip -> we don't want duplicates
    return organized

def get_reprepped(project):
    pass

def get_not_organized(project):
    flowcells_samples = get_sequenced(project)
    project_path = os.path.join('/proj/ngi2016003/nobackup/NGI/DATA', project)
    not_organized = {}
    for sample in flowcells_samples:
        for fc in flowcells_samples[sample]:
            # '*'' is libprep, can be 'A', 'B', etc
            path = os.path.join(project_path, sample, '*', fc)
            if not glob.glob(path):
                if sample not in not_organized:
                    not_organized[sample] = [fc]
                else:
                    not_organized[sample].append(fc)
    return not_organized

if __name__ == '__main__':
    parser = argparse.ArgumentParser("""Process one or more project and report basic statistiscs for it """)
    parser.add_argument('projects', metavar='project', type=str, nargs='+', help='Projects we want to have statistics for (P1111)')
    parser.add_argument('--project-status', help="reports number of samples, of samples-runs, analysed samples and delivered samples (work only if a single project is specified)", action='store_true')
    parser.add_argument('--skip-header', help="skip header", action='store_true')

    # added by Kate
    parser.add_argument('--incoherent', help="Project-status but only for samples which have incoherent number of sequenced/organized/analyzed", action="store_true")
    parser.add_argument('--undetermined', help="List of the samples which use undetermined", action="store_true")
    parser.add_argument('--sequenced', help="List of all the sequenced samples", action="store_true")
    parser.add_argument('--resequenced', help="List of samples that have been sequenced more than once, and the flowcells", action="store_true")
    parser.add_argument('--organized', help="List of all the organized samples and the flowcells", action="store_true")
    parser.add_argument('--to-organize', help="List of all the not-organized samples and flowcells", action="store_true")
    parser.add_argument('--analyzed', help="List of all the analysed samples", action="store_true")
    parser.add_argument('--to-analyze', help="List of samples that are ready to be analyzed", action="store_true")
    parser.add_argument('--analysis-failed', help="List of all the samples with failed analysis (with exit code != 0 or empty exit code for samples not under analysis", action="store_true")
    parser.add_argument('--under-analysis', help="List of the samples under analysis", action="store_true")
    parser.add_argument('--under-qc', help="List of samples under qc. Use for projects without BP", action="store_true")

    parser.add_argument('--low-coverage', help="List of analyzed samples with coverage below 28.5X", action="store_true")
    parser.add_argument('--low-mapping', help="List of all the samples with mapping below 97 percent", action="store_true")
    parser.add_argument('--flowcells', help="List of flowcells where each sample has been sequenced", action="store_true")

    # todo
    parser.add_argument('--high-duplicates', help="List of the samples with high percentage of duplicates (more than 15 percent)", action="store_true")
    parser.add_argument('--to-sequence', help="List of the samples that are not sequenced AT ALL on ANY flowcells or lanes. Not implemented yet", action="store_true")
    parser.add_argument('--qc-done', help="List of samples with completed QC. Not implemented yet", action="store_true")
    parser.add_argument('--sample', '-s', type=str, help="Statistics for the specified sample. Not implemented yet")

    args = parser.parse_args()
    if not args.projects:
        print("ERROR: project must be specified")
        sys.exit()

    # parse arguments
    project = args.projects[0]
    data_dir = "/proj/{}/nobackup/NGI/DATA/".format(uppmax_id)
    analysis_dir = "/proj/{}/nobackup/NGI/ANALYSIS/".format(uppmax_id)


    # output the result
    if args.low_coverage:
        all_results = find_results_from_francesco(uppmax_id, project)
        samples = get_low_coverage(project, all_results)
        if samples:
            if not args.skip_header:
                print("Coverage below 28.5X:")
            for sample in sorted(samples.keys()):
                print("{}   {}".format(sample, samples[sample]))
        else:
            print('All samples are above 28.5X')

    elif args.sequenced:
        flowcells_samples = get_sequenced(project) # from incoming
        if flowcells_samples:
            if not args.skip_header:
                print('Sequenced samples')
            for sample, flowcells in flowcells_samples.items():
                print("{}: {}".format(sample, ' '.join(sorted(flowcells))))
        else:
            print('No samples sequenced')

    elif args.resequenced:
        sequenced = get_sequenced(project)
        resequenced = {}
        for sample, flowcells in sequenced.items():
            if len(flowcells) > 1:
                resequenced[sample] = flowcells
        if resequenced:
            if not args.skip_header:
                print('Resequenced samples')
            for sample, flowcells in sorted(resequenced.items(), key=lambda x:x[0]):
                print("{}: {}".format(sample, ' '.join(sorted(flowcells))))

    elif args.organized:
        # todo: print by flowcell, not by sample
        organized = get_organized(project)
        if organized:
            if not args.skip_header:
                print('Organized flowcells/samples:')
            for sample, flowcells in sorted(organized.items(), key=lambda x:x[0]):
                print("{}: {}".format(sample, ' '.join(sorted(flowcells))))
        else:
            print('No organized samples')

    elif args.to_organize:
        result = get_not_organized(project)
        if result:
            if not args.skip_header:
                print('Samples to be organized:')
            for sample, flowcells in result.items():
                print("{}: {}".format(sample, ' '.join(flowcells)))
        else:
            print('All samples organized')

    elif args.analyzed:
        samples = find_results_from_francesco(uppmax_id, project)
        analyzed_samples = []
        sequenced_samples = []
        for sample_id, sample in samples.items():
            sequenced = sample.get('#Archived_runs', '')
            organized = sample.get('#Data_runs', '')
            analyzed = sample.get('#Analysis_runs', '')
            if sequenced and organized and analyzed:
                if sequenced == organized == analyzed:
                    analyzed_samples.append(sample_id)
            if sample_id not in sequenced_samples:
                sequenced_samples.append(sample_id)

        if set(analyzed_samples) == set(sequenced_samples) != set([]):
            print('All {} samples analyzed'.format(len(analyzed_samples)))
        elif analyzed_samples:
            if not args.skip_header:
                print('Analyzed samples:')
            for sample in sorted(analyzed_samples):
                print(sample)
            if not args.skip_header:
                print('{}/{} (analyzed/sequenced) samples have been analyzed.'.format(len(analyzed_samples), len(sequenced_samples)))
                print('Check --to-analyze, --to-organize, --analysis-failed')
        else:
            print('No analyzed samples')

    elif args.undetermined:
        result = get_samples_with_undetermined(data_dir, project)
        if result:
            if not args.skip_header:
                print('Organized with undetermined:')
            for sample in sorted(result.keys()):
                print("{}: {}".format(sample, ", ".join(fc for fc in result[sample])))
        else:
            print('No undetermined used')

    elif args.under_analysis:
        result = get_samples_under_analysis(project)
        if result:
            if not args.skip_header:
                print('Samples under analysis:')
            for sample in sorted(result):
                print(sample)
        else:
            print('No samples are being analyzed')

    elif args.to_analyze:
        samples = find_results_from_francesco(uppmax_id, project)
        samples_to_analyze = []
        for sample_id, sample in samples.items():
            organized = sample.get('#Data_runs', '')
            analyzed = sample.get('#Analysis_runs', '')
            if organized > analyzed:
                samples_to_analyze.append(sample_id)

        if samples_to_analyze:
            if not args.skip_header:
                print('Samples ready to be analyzed:')
            for sample in sorted(samples_to_analyze):
                print(sample)
        else:
            print('No samples ready to be analyzed. Check --to-organize or --analyzed')

    elif args.under_qc:
        result = get_samples_under_qc(project)
        if result:
            if not args.skip_header:
                print('Samples under QC:')
            for sample in sorted(result):
                print(sample)
        else:
            print('No samples under QC')

    elif args.analysis_failed:
        result = get_samples_with_failed_analysis(project, analysis_dir)
        if result:
            if not args.skip_header:
                print('Samples with failed analysis:')
            for sample in sorted(result):
                print(sample, result[sample])
        else:
            print('No analysis failed')

    elif args.incoherent:
        results_francesco = find_results_from_francesco(uppmax_id, project)
        result = get_incoherent_samples(results_francesco)
        if result:
            if not args.skip_header:
                print("Samples with incoherent runs:")
            for sample in sorted(result.keys()):
                numbers = result[sample]
                print("{}\t{}\t{}\t{}".format(sample, numbers['sequenced'], numbers['organized'], numbers['analyzed']))
        else:
            print("All samples should be fine.")

    elif args.low_mapping:
        result = find_results_from_francesco(uppmax_id, project)

        low_mapping = {}
        for sample_id, sample in result.items():
            mapping = sample.get('%AlignedReads', '')
            try:
                mapping = float(mapping)
            # add strange values as well (if something is wrong, we can see it)
            except ValueError as e:
                low_mapping[sample_id] = mapping
            else:
                if mapping < 97.0:
                    low_mapping[sample_id] = mapping

        if low_mapping:
            if not args.skip_header:
                print("Samples with low mapping (<97%):")
            for sample, mapping in sorted(low_mapping.items(), key=lambda x:x[1], reverse=True):
                print(sample, low_mapping[sample])
        else:
            print('All samples mapped more than 97%')

    elif args.flowcells:
        result = get_sequenced(project)
        if result:
            for sample in sorted(list(result.keys())):
                print('{} {}'.format(sample, ' '.join(result[sample])))
        else:
            print('Something was wrong? No flowcells in the result')

    elif args.to_sequence:
        print("--to-sequence has not been implemented yet")

    elif args.sample:
        # todo sequenced on flowcells, organized on flowcells, undetermined, coverage, duplicates, mapping,
        # todo: under analysis, analysis failed
        # stats from Francesco's script - done
        # + sequenced on flowcells
        # + organized on flowcells - done
        # sequenced, but not organized - done
        # undetermined
        result = find_results_from_francesco(uppmax_id, project)
        sample = args.sample
        sample_entry = result.get(sample, {})
        if sample_entry:
            if not args.skip_header:
                print("sample_name\t#Reads\tRaw_coverage\t#Aligned_reads\t%Aligned_reads\tAlign_cov\tAutosomalCoverage\t%Dup\tMedianInsertSize")
            print("{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}".format(
                    sample,
                    sample_entry.get('#Reads'),
                    sample_entry.get('RowCov'),
                    sample_entry.get('#AlignedReads'),
                    sample_entry.get('%AlignedReads'),
                    sample_entry.get('AlignCov'),
                    sample_entry.get('AutosomalCoverage'),
                    sample_entry.get('%Dup'),
                    sample_entry.get('MedianInsertSize')
                ))
        else:
            print('No stats for sample {}'.format(sample))

        sequenced = get_sequenced(project)
        flowcells = sequenced.get(sample, {})
        if flowcells:
            print('Sequenced on flowcells:')
            for flowcell in sorted(flowcells):
                print(' {}'.format(flowcell))
        else:
            print('Nothing sequenced')

        organized = get_organized(project)
        flowcells = organized.get(sample, {})
        if flowcells:
            print('Organized on flowcells:')
            for flowcell in sorted(flowcells):
                print(' {}'.format(flowcell))
        else:
            print('Nothing organized')
    else:
        result = find_results_from_francesco(uppmax_id, project)
        if not args.skip_header:
            print("sample_name\t#Reads\tRaw_coverage\t#Aligned_reads\t%Aligned_reads\tAlign_cov\tAutosomalCoverage\t%Dup\tMedianInsertSize")
        for sample, sample_entry in result.items():
            print("{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}".format(
                    sample,
                    sample_entry.get('#Reads'),
                    sample_entry.get('RowCov'),
                    sample_entry.get('#AlignedReads'),
                    sample_entry.get('%AlignedReads'),
                    sample_entry.get('AlignCov'),
                    sample_entry.get('AutosomalCoverage'),
                    sample_entry.get('%Dup'),
                    sample_entry.get('MedianInsertSize')
                ))
