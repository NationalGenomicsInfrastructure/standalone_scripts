#!/usr/bin/env/python

import re
import os
import sys
import shutil
import argparse
import collections
def merge_files(input_dir, dest_dir):

    #Gather all fastq files in inputdir and its subdirs
    fastq_files=[]
    for subdir, dirs, files in os.walk(input_dir):
        for fastq in  files:
            if fastq.endswith('.fastq.gz'):
                fastq_files.append(os.path.join(subdir, fastq))

    #Match NGI sample number from flowcell
    sample_pattern=re.compile("(P[0-9]+_[0-9]+)_([1-2])")
    #Remove files that already have the right name (i.e have been merged already)
    matches=[]
    for fastq_file in fastq_files:
        try:
            match=sample_pattern.search(os.path.basename(fastq_file)).group(1)
            if match:
                matches.append(fastq_file)
        except AttributeError:
            continue
    fastq_files=matches

    while fastq_files:
        tomerge=[]

        #grab one sample to work on
        first=fastq_files[0]
        fq_bn=os.path.basename(first)
        sample_name=sample_pattern.search(fq_bn).group(1)
        fastq_files_read1=[]
        fastq_files_read2=[]

        for fq in fastq_files:
            if sample_name in os.path.basename(fq) and "_1." in os.path.basename(fq):
                fastq_files_read1.append(fq)

            if sample_name in os.path.basename(fq) and "_2." in os.path.basename(fq):
                fastq_files_read2.append(fq)

        fastq_files_read1.sort()
        fastq_files_read2.sort()
        actual_merging(sample_name,1, fastq_files_read1, dest_dir)
        actual_merging(sample_name,2, fastq_files_read2, dest_dir)

        for fq in fastq_files_read1:
            fastq_files.remove(fq)
        for fq in fastq_files_read2:
            fastq_files.remove(fq)


def actual_merging(sample_name, read_nb, tomerge, dest_dir):
    outfile=os.path.join(dest_dir, "{}_R{}.fastq.gz".format(sample_name, read_nb))
    print("Merging the following files:")
    if not tomerge:
        print("No read {} files found".format(read_nb))
        return
    for fq in tomerge:
        print(fq)
    print("as {}".format(outfile))
    with open(outfile, 'wb') as wfp:
        for fn in tomerge:
            with open(fn, 'rb') as rfp:
                shutil.copyfileobj(rfp, wfp)


if __name__ == "__main__":
   parser = argparse.ArgumentParser(description=""" Merges all fastq-files from each samples into one file. Looks through the given dir and subdirs.
   Written with a the NGI folder structure in mind.""")
   parser.add_argument("input_dir", metavar='Input directory', nargs='?', default='.',
                                   help="Base directory for the fastq files that should be merged. ")
   parser.add_argument("dest_dir", metavar='Output directory', nargs='?', default='.',
                                   help="Path path to where the merged files should be outputed. ")
   args = parser.parse_args()
   merge_files(args.input_dir, args.dest_dir)
