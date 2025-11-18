#!/usr/bin/env python

# load libraries
from __future__ import print_function
import argparse
from openpyxl import load_workbook
import coloredlogs
import logging
import re
import couchdb
import numbers
import yaml

# global variables
WARNINGS = 0
NONNUMERIC = []
EMPTY = []
BADRIN = []
OUTCONC = []
OUTVOL = []

# Set up a logger with colored output
logger = logging.getLogger(__name__)
logger.propagate = False  # Otherwise the messages appeared twice
coloredlogs.install(level='INFO', logger=logger,
                    fmt='%(asctime)s %(levelname)s %(message)s')


class ProjectSheet:
    #Class Attributes
    SHEET_NAME = 'Sample information'
    FIRST_LINE = 20  # First line where user submitted data is located
    SAMPLE_NAME_COL = 'O'
    A_RATIO_COL = 'R'  # A260:A280 ratio
    CONC_COL = 'S'
    VOL_COL = 'T'
    RIN_COL = 'V'
    SAMPLE_TYPE = 'O8'
    PLATE_ID = 'M6'
    PROJECT_NAME_USER_SHEET = 'M3'

    # Initializer / Instance attributes
    def __init__(self, sample_info_sheet):
        self.sample_info_sheet = sample_info_sheet
        self.work_sheet = None
        self.sample_rec = None

    # instance methods
    def getAccessUserSheet(self):
        """loads the Excel sheet"""
        if self.work_sheet is None:
            wb = load_workbook(self.sample_info_sheet, read_only=True, data_only=True)
            ws = wb[ProjectSheet.SHEET_NAME]
            self.work_sheet = ws

    def projectID(self):
        """retrieves the project and plate ID from the excel sheet and checks the
        correctness of the plate ID format."""
        self.getAccessUserSheet()
        plate_id = self.work_sheet[ProjectSheet.PLATE_ID].value
        if(len(re.findall('P\d+P\d+', plate_id))>0):
            project_id_user = re.findall('P\d+', plate_id)[0]
        else:
            logger.error(
                'Given plate ID ({}) in cell {} has the wrong format. It should be in the format'
                ' PxxxxxPx, where x are numbers. If your Plate ID is correct, contact your project coordinator.'\
                .format(plate_id, ProjectSheet.PLATE_ID)
                )
            quit()
        return([project_id_user, plate_id])

    def getSamples(self):
        """ identifies the all rows containing a sample name, discards rows without entry.
        Rows containing whitespace only trigger a warning and are discarded for subsequent
        tests """
        cellID_withSample = []
        cellID_noSample = []
        for i in range(ProjectSheet.FIRST_LINE, ProjectSheet.FIRST_LINE+96):
            cell_id = "{col}{row_iter}".format(col=ProjectSheet.SAMPLE_NAME_COL, row_iter=i)
            cell_value = str(self.work_sheet[cell_id].value)
            if(cell_value.isspace()):
                logger.warning(
                    'Cell {} contains empty spaces only. Remove content.'.format(cell_id)
                    )
                global WARNINGS
                WARNINGS += 1
            elif(self.work_sheet[cell_id].value is not None):
                cellID_withSample.append(i)
            else:
                cellID_noSample.append(cell_id) # TODO check here that these rows do really not contain information
        return(cellID_withSample)

    def ProjectInfo(self, config):
        """
        Retrieves the project information from couchDB, checks that the project exists in
        couchDB and is unique. Returns the information and the full project plateID.
        """
        with open(config) as settings_file:
            server_settings = yaml.load(settings_file, Loader=yaml.FullLoader)
        couch = couchdb.Server(server_settings.get("couch_server", None))
        db = couch["projects"]
        # check the existence of the project number in couchDB
        project_plate_ID = self.projectID()
        project_id_found = db.view("project/project_id", key=project_plate_ID[0])
        prow = project_id_found.rows
        # Project not found
        if len(prow) == 0:
            logger.error(
                'Project not found, please check your entry for the PlateID, it should have the format'
                'PxxxxxPx, where x are numbers. If your Plate ID is correct, contact your project coordinator.'
                )
            quit()
        # more than one project found
        elif len(prow) > 1:
            logger.error(
                'Project ID not unique, please check your entry for the PlateID, it should have the format'
                'PxxxxxPx, where x are numbers. If your Plate ID is correct, contact your project coordinator.'
                )
            quit()
        else:
            # puts the Document of the identified project in a new variable "pdoc"
            pdoc = db.get(prow[0].id)
            return pdoc, project_plate_ID[1]

    def prep_standards(self, info, config):
        '''
        gets the sample requirements from statusDB (json format) based
        on the given sample prep type.
        '''
        with open(config) as settings_file:
            server_settings = yaml.load(settings_file, Loader=yaml.FullLoader)
        couch = couchdb.Server(server_settings.get("couch_server", None))
        requirementsDB = couch["sample_requirements"]
        requirements = requirementsDB.view("valid/by_date", descending=True)
        recom_info = requirements.rows[0].value["requirements"]

        prep = info['details']['library_construction_method']
        prep_recs = [None,None,None,None,None,None,None]
        if prep in recom_info:
            if recom_info[prep]['Quality requirement'] is not None:
                prep_recs = [\
                recom_info[prep]['Concentration']['Minimum'],
                recom_info[prep]['Concentration']['Maximum'],
                recom_info[prep]['Volume']['Minimum'],
                recom_info[prep]['Amount']['Recommended'],
                recom_info[prep]['Amount']['Minimum'],
                recom_info[prep]['Quality requirement']['Method'],
                recom_info[prep]['QC recommendation']]
                if 'RIN' in recom_info[prep]['Quality requirement']:
                    prep_recs.append(recom_info[prep]['Quality requirement']['RIN'])
                else:
                    prep_recs.append(None)
            else:
                prep_recs = [\
                recom_info[prep]['Concentration']['Minimum'],
                recom_info[prep]['Concentration']['Maximum'],
                recom_info[prep]['Volume']['Minimum'],
                recom_info[prep]['Amount']['Recommended'],
                recom_info[prep]['Amount']['Minimum'],
                None,
                recom_info[prep]['QC recommendation'],
                None]

        else:
            logger.error('Preparation type \"{}\" not found'.format(prep))
            quit()
        return(prep_recs)

    def validate_project_Name(self, info, project_plate_ID ):
        """
        Prints the identified project name based on the user supplied Plate/Project ID for
        control purposes by the project coordinator. Further checks that the
        plate number is not already in couchDB.
        """
        project_name_DB = info['project_name']
        samples = list(info['samples'].keys())
        plate ='P{}_{}'.format(project_plate_ID.split("P")[1],project_plate_ID.split("P")[2])
        found_plate = [s for s in samples if plate in s]
        if(len(found_plate)>0):
            new_plate_no = int(project_plate_ID.split("P")[2])
            new_plate_no += 1
            new_plate_ID = 'P{}P{}'.format(project_plate_ID.split("P")[1], new_plate_no)
            logger.warning(
                'Plate number {} is already used. Please increase the plate number to {}.'.format(project_plate_ID, new_plate_ID))
            global WARNINGS
            WARNINGS += 1
        logger.info('identified project name: {}'.format(project_name_DB))



    def validate(self, info, config_info):
        """Validates all rows with a sample ID
        Given the column letter and which rows to validate:
        - Initiates the given validators for cell content (numeric), concentration,
        volume and RIN (RNA samples only) with the optional attributes.
        - Loops through all the given cells and validates them individually.
        - prints summaries of the warnings and of the Excel file check.
        """
        prep_recs = self.prep_standards(info, config_info)
        passes = 0
        total = 0
        recom_avail = 1
        for row_nr in self.getSamples():
            total += 1

            cell_id_conc = "{col}{row_nr}".format(col=ProjectSheet.CONC_COL, row_nr=row_nr)
            cell_id_vol = "{col}{row_nr}".format(col=ProjectSheet.VOL_COL, row_nr=row_nr)
            cell_id_rin = "{col}{row_nr}".format(col=ProjectSheet.RIN_COL, row_nr=row_nr)
            validator = Validator(self.work_sheet,cell_id_conc,cell_id_vol, cell_id_rin)
            result_numeric = validator.validate_numeric()

            if any(t is not None for t in prep_recs[0:7]):
                result_conc = validator.validate_conc(prep_recs[0], prep_recs[1])
                result_vol = validator.validate_vol(prep_recs[2])
                if prep_recs[7] is not None:
                    result_rin = validator.validate_rin(prep_recs[7])
                    if result_conc and result_vol and result_rin and result_numeric:  # Test passed
                        passes += 1
                else:
                    if any(t is not None for t in prep_recs):
                        if result_conc and result_vol and result_numeric:  # Test passed
                            passes += 1
            else:
                # this means that the prep chosen has no recommendations in the DB
                # i.e. ALL values for recommendations are missing
                recom_avail = None
                if result_numeric:  # Test passed
                    passes += 1

        # summary of QC prerequisits and recommendations
        if (prep_recs[5] is not None):
            logger.info(
                'Sample processing prerequisit: submission of {} data'.format(prep_recs[5])
                )
        if (prep_recs[6] is not None):
            logger.info(
                'Sample QC recommendation: submission of {} data'.format(prep_recs[6])
                )
        # summary of all warnings
        if (len(EMPTY) > 0):
            logger.warning(
                'Required entries in the following cells are missing: {}'\
                .format(EMPTY)
                )
        if (len(NONNUMERIC) > 0):
            logger.warning(
                'Required entries in the following cells are non-numeric: {}'\
                .format(NONNUMERIC)
                )
        if (len(OUTCONC) > 0):
            logger.warning(
                'Sample concentration(s) in cell(s) {} is out of specifications: {}-{}ng/ul'\
                .format(OUTCONC, prep_recs[0], prep_recs[1])
                )
        if (len(OUTVOL) > 0):
            logger.warning(
                'Sample volume(s) in cell(s) {} is to low: min volume = {}ul'\
                .format(OUTVOL, prep_recs[2])
                 )
        if (len(BADRIN) > 0):
            logger.warning(
                'RIN value in cell(s) {} is below recommendation'\
                .format(BADRIN)
                )

        # summary for missing sample recommendations
        if (recom_avail is None):
            logger.info(
                'Sample submission check complete. No sample recommendations available. {}/{} pass, {} warnings(s)'\
                .format(passes, total, WARNINGS)
                )
        # summary with sample recommendations
        else:
            logger.info(
                'Sample submission check complete. {}/{} pass, {} warning(s).'\
                .format(passes, total, WARNINGS)
                )


class Validator(object):
    # Initializer / Instance attributes
    def __init__(self, access_sample_info_sheet, concentrationID, volumeID, rinID):
        self.access_sample_info_sheet = access_sample_info_sheet
        self.concentrationID = concentrationID
        self.volumeID = volumeID
        self.rinID = rinID

    # instance methods
    def validate_numeric(self):
        global WARNINGS
        global EMPTY
        global NONNUMERIC
        """Checks whether value is numeric or not."""
        warnings_before = WARNINGS
        for checkNumbers in [self.concentrationID, self.volumeID, self.rinID]:
            if not isinstance(self.access_sample_info_sheet[checkNumbers].value, numbers.Number):
                try:
                    float(self.access_sample_info_sheet[checkNumbers].value.replace(",", "."))
                    WARNINGS += 1
                    NONNUMERIC.append(checkNumbers)
                except ValueError:
                    WARNINGS += 1
                    NONNUMERIC.append(checkNumbers)
                except AttributeError:
                    if self.access_sample_info_sheet[checkNumbers].value is None:
                        WARNINGS += 1
                        EMPTY.append(checkNumbers)
                    else:
                        raise
        if (WARNINGS > warnings_before):
            return False
        else:
            return True

    def validate_conc(self, min_conc, max_conc):
        """checks entry for concentration"""
        if not(self.concentrationID in NONNUMERIC or self.concentrationID in EMPTY):
            global WARNINGS
            global OUTCONC
            if(self.access_sample_info_sheet[self.concentrationID].value < min_conc) \
            or (self.access_sample_info_sheet[self.concentrationID].value > max_conc):
                WARNINGS += 1
                OUTCONC.append(self.concentrationID)
                return False
            else:
                return True

    def validate_vol(self, vol):
        """Checks entry for volume"""
        if not(self.volumeID in NONNUMERIC or self.volumeID in EMPTY):
            global WARNINGS
            global OUTVOL
            if(self.access_sample_info_sheet[self.volumeID].value < vol):
                WARNINGS += 1
                OUTVOL.append(self.volumeID)
                return False
            else:
                return True

    def validate_rin(self, rin):
        """Checks entry for RIN in RNA samples only"""
        if self.access_sample_info_sheet[self.rinID].value < rin:
            global WARNINGS
            global BADRIN
            WARNINGS += 1
            BADRIN.append(self.rinID)
            return False
        else:
            return True


def main(input_sheet, config_statusDB):
    # Instantiate the ProjectSheet object
    sheetOI = ProjectSheet(input_sheet)
    # get Project Information from couchDB
    Project_Information, project_plate_ID = sheetOI.ProjectInfo(config_statusDB)
    # validate the project name to ensure correct identification in couchDB
    sheetOI.validate_project_Name(Project_Information, project_plate_ID)
    # validate all entries
    sheetOI.validate(Project_Information, config_statusDB)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sampleInfoSheet',
                        help="Completed sample info sent to NGI by the user.")
    parser.add_argument('config_statusDB',
                        help="settings file in yaml format to access statusDB \
                        in the format \"couch_server: https://<username>:<password>@ngi-statusdb.scilifelab.se\"")
    args = parser.parse_args()

    main(args.sampleInfoSheet,  args.config_statusDB)
