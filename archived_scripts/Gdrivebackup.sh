#!/bin/bash

###
### This script is to zip and save certain directories from Google Drive which have been backed up to 'ngi.transfer' to the homer system for QA purposes
### This script requires that The Google Backup and Sysnc utility be running on the ngi.transfer computer and that ngi.transfer has access to homer
### Recommended to be run once a month. The file `~/Library/Preferences/nsmb.conf` contains the password details for mount_smbfs.
###

currentDate=`date +"%Y-%m-%d"`
echo 'Run $currentDate'
GDRIVE="$HOME/Google Drive/My Drive"
#Zip GDrive Sync file
zip -vr $HOME/opt/zipFilesTemp/QAbackup.$currentDate.zip  $GDRIVE/Electronic\ logbooks\ \(QA\ system\) \
        $GDRIVE/Genomics\ Platform $GDRIVE/_Quality\ assurance\ system

#make mountpoint
mkdir -p $HOME/kvalitetssystem

#export the password from the file `~/Library/Preferences/nsmb.conf`
export $(sed -n 2p /Users/ngi.transfer/Library/Preferences/nsmb.conf)

#Mount homer
/sbin/mount -t smbfs //ngi.transfer:$password@homer.scilifelab.se/kvalitetssystem $HOME/kvalitetssystem

#Copy zipped backup file
rsync -av $HOME/opt/zipFilesTemp/QAbackup.$currentDate.zip $HOME/kvalitetssystem

#Unmount homer
/sbin/umount $HOME/kvalitetssystem

#rm zip file
rm $HOME/opt/zipFilesTemp/QAbackup.$currentDate.zip
