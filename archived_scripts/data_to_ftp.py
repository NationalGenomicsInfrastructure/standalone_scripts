#!usr/bin/env -python
import os
import sys
import argparse
import subprocess
from ftplib import FTP
try:
    import ConfigParser
except ImportError:
    import configparser

DESCRIPTION = """\
A stand alone script to deliver the data to a ftp server provided by USER outside
Sweden or from some other reason who dont have acces to UPPMAX. It is neccesary
before running this script the fake/real delivery to am UPPMAX projects INBOX
should be done.

[ftp]
domain: xxxx
port: xxxx
username: xxxx
password: xxxx
project: xxxx
uppmaxid: xxxx"""

config_file_format = """Input config file should be formated as mentioned in description, especially the
                        key/header names should be same as in the example, else the list 'config_keys'
                        in source code should be changed to match the key names as in config file"""

## command line arugments for the script ##
parser = argparse.ArgumentParser(description=DESCRIPTION,formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument('config_file',type=str,help=config_file_format)
parser.add_argument('--exclude_sample',type=str,default=None,help="Samples to be exculded from tranfser, can be single sample \
                                                                     name or multiple sample name seperated by comma(,).")
parser.add_argument('--only_sample',type=str,default=None,help="Sample(s) that have to copied ignoring other in the INBOX. \
                                                                Multiple sample names are given by comma separated.")
parser.add_argument('--no_reports',default=False,action='store_true',help="Dont tranfer/copy report in INBOX")
parser.add_argument('--no_md5check',default=False,action='store_true',help="Dont check for md5sum files for fastq files, by default it does.")
args = parser.parse_args()

## method to check and parse config file ##
def get_config_info(config_file,check_keys):
    try:
        config = ConfigParser.ConfigParser()
    except NameError:
        config = configparser.ConfigParser()
    config_path = config.read(config_file)
    try:
        return {k:config.get('ftp',k) for k in check_keys}
    except:
        sys.exit("Check the keys in config file, see help for more info regarding the format")

## method to create a md5sum file for given name and given file ##
def do_md5sum(fq,md5):
    try:
        cmd =  subprocess.Popen(["md5sum", fq],stdout=subprocess.PIPE)
        cmd.wait()
        md5_op = cmd.communicate()[0].strip()
        with open(md5,'w') as op_file:
            op_file.write(md5_op)
        return True
    except:
        return False

config_keys = ["domain", "port", "username", "password", "project", "uppmaxid"]
config_file = args.config_file
exculde_samples = args.exclude_sample.split(',') if args.exclude_sample else []
config_info = get_config_info(config_file,config_keys)
proj = config_info.get('project')
sm_cnt = 0
pj_dir = "/proj/{}/INBOX/{}".format(config_info.get('uppmaxid'),proj)
os.chdir(pj_dir)
if args.only_sample:
    samples = args.only_sample.split(',')
else:
    samples = [item for item in os.listdir(os.getcwd()) if os.path.isdir(item) and item not in exculde_samples]
reports = [item for item in os.listdir(os.getcwd()) if item.endswith('.pdf')] if not args.no_reports else []

print("Total {} samples and {} reports are going to be copied now for project {}".format(len(samples),len(reports),proj))

## opening a ftp session ##
ftp = FTP()
ftp.connect(config_info.get('domain'),config_info.get('port'))
ftp.login(config_info.get('username'),config_info.get('password'))
ftp.cwd('/')
try:
    ftp.mkd(proj)
    print("Project folder has been created for {} in remote..".format(proj))
except:
    print("Project folder already exists for {} in remote..".format(proj))
    pass
ftp.cwd(proj)
## transfer reports if not otherwise mentioned
for report in reports:
    with open(report,'rb') as rep:
        ftp.storbinary("STOR {}".format(report),rep)

## transfer fastq files for samples
for sam in samples:
    os.chdir(pj_dir)
    ftp.cwd("/{}".format(proj))
    try:
        ftp.mkd(sam)
        ftp.cwd(sam)
        print("Process for samples {} have started..".format(sam))
    except:
        print("Sample {} already exists in remote.. skipping..".format(sam))
        continue
    flowcell = os.listdir(sam)
    for fc in flowcell:
        os.chdir(os.path.join(pj_dir,sam,fc))
        ftp.cwd("/{}/{}".format(proj,sam))
        ftp.mkd(fc)
        ftp.cwd(fc)
        fq_files = [fl for fl in os.listdir(os.getcwd()) if fl.endswith('.fastq.gz')]
        for fq in fq_files:
            if not args.no_md5check:
                md5 = "{}.md5".format(fq)
                if not os.path.exists(md5):
                    fq_md5 = do_md5sum(fq,md5)
                    if not fq_md5:
                        sys.exit("Could not create md5sum for sample {} in flowcell {} and file {}".format(sam,fc,fq))
                with open(md5, 'rb') as md5_file:
                    ftp.storbinary("STOR {}".format(md5),md5_file)
            with open(fq,'rb') as fq_file:
                ftp.storbinary("STOR {}".format(fq),fq_file)
        print("Data uploaded for sample {} in flowcell {}".format(sam,fc))
    print("All data for sample {} is now completed..".format(sam))
    sm_cnt += 1
ftp.quit()
print("Transfer done, total {}/{} samples proccessed".format(sm_cnt,len(samples)))
