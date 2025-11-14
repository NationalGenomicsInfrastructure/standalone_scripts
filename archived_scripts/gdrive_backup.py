#!/usr/bin/python

### WIP, do not use!!


import sys, httplib2, os, datetime, io, socket
from time import gmtime, strftime
from apiclient import discovery
import oauth2client
from oauth2client import client
from oauth2client import tools
from oauth2client.file import Storage
from datetime import date
import argparse

from apiclient.discovery import build
import pdb

CLIENT_SECRET_FILE = 'client_id.json'
TOKEN_FILE="drive_api_token.json"
SCOPES = 'https://www.googleapis.com/auth/drive'
APPLICATION_NAME = 'GDrive'


def main():
    """
    """
    credentials = get_credentials()
    http = credentials.authorize(httplib2.Http())
    discoveryUrl = ('https://drive.googleapis.com/$discovery/rest?'
                    'version=v3')
    pdb.set_trace()
    service = discovery.build('drive', 'v3', http=http)
    listfiles(service)



def listfiles(service):
    pdb.set_trace()
    results = service.files().list(fields="nextPageToken, files(id, name,mimeType)").execute()
    items = results.get('files', [])
    if not items:
        print('No files found.')
    else:
        print('Files:')
        print('Filename (File ID)')
        for item in items:
            print('{0} ({1})'.format(item['name'].encode('utf-8'), item['id']))
        print('Total=', len(items))



def get_credentials():

    home_dir = os.path.expanduser('~')
    credential_dir = os.path.join(home_dir, '.credentials')
    if not os.path.exists(credential_dir):
        os.makedirs(credential_dir)
    credential_path = os.path.join(credential_dir,
                                   'drive-python-quickstart.json')

    store = oauth2client.file.Storage(credential_path)
    credentials = store.get()
    if not credentials or credentials.invalid:
        flow = client.flow_from_clientsecrets(CLIENT_SECRET_FILE, SCOPES)
        flow.user_agent = APPLICATION_NAME
        # if flags:
        credentials = tools.run_flow(flow, store )
        # else:  # Needed only for compatibility with Python 2.6
        #     credentials = tools.run(flow, store)
        print('Storing credentials to ' + credential_path)
    return credentials



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="""This script lists and dowloads all shared documents in a users Gdrive. 
    """)
    #parser.add_argument("input_dir", metavar='Input directory', nargs='?', default='.',
    #                               help="Base directory for the fastq files that should be merged. ")
    #parser.add_argument("dest_dir", metavar='Output directory', nargs='?', default='.',
    #                               help="Path path to where the merged files should be outputed. ")
    #args = parser.parse_args() 
    #merge_files(args.input_dir, args.dest_dir)
    main()
