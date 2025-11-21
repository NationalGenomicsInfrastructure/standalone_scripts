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
import decimal
import yaml
from numpy import setdiff1d
from collections import Counter
import Levenshtein as lev
import sys

# Set up a logger with colored output
logger = logging.getLogger(__name__)
logger.propagate = False  # Otherwise the messages appeared twice
coloredlogs.install(level='INFO', logger=logger,
                    fmt='%(asctime)s %(levelname)s %(message)s')


class LibrarySheet:
#Class Attributes
    SHEET_NAME = 'Sample information'
    FIRST_LINE = 20  # First line where user submitted data is located
    SAMPLE_NAME_COL = 'P' # user defined sample name
    POOL_NAME_COL = 'V' # column of pool names in pool summary
    POOL_NAME_SAMPLE_COL = "O" # column of pool names in index definition
    LENGTH_COL = 'Y'  # average fragment length of pools
    MOLARITY_COL = 'AA'  # molarity of the pool, currently not used
    SINDEX_COL = 'S' # NGI standard index sequences (automated)
    CINDEX_COL = 'T' # custom index sequences
    PLATE_ID = 'N6' # plate ID as specified in user sheet
    MAX_DISTANCE = 2 # required differences between Indexes in the same pool

    # Initializer / Instance attributes
    def __init__(self, library_info_sheet):
        self.library_info_sheet = library_info_sheet
        self.library_sheet = None
        self.sample_rec = None
        self.Project_Information = None

    # instance methods
    def getAccessUserSheet(self):
        if self.library_sheet == None:
            wb = load_workbook(self.library_info_sheet, read_only=True, data_only=True)
            ws = wb[LibrarySheet.SHEET_NAME]
            self.library_sheet = ws

    def projectID(self):
        """retrieves the project and plate ID from the excel sheet and checks the
        correctness of the plate ID format."""
        self.getAccessUserSheet()
        plate_id = self.library_sheet[LibrarySheet.PLATE_ID].value
        if(len(re.findall('P\d+P\d+', plate_id))>0):
            project_id_user = re.findall('P\d+', plate_id)[0]
        else:
            logger.error(
                'The given plate ID ({}) in cell {} has the wrong format. It should be in the format'
                ' PxxxxxPx, where x are numbers. If you think your Plate ID is correct, contact your project coordinator.'\
                .format(plate_id, LibrarySheet.PLATE_ID)
                )
            sys.exit(1)
        return([project_id_user, plate_id])

    def getRows(self, column):
        """ identifies all rows containing a sample name, discards rows without entry.
        Rows containing whitespace only trigger a warning and are disregarded in subsequent
        tests """
        warning_empty_row = 0
        cellID_withSample = list()
        cellID_noSample = list()
        for i in range(LibrarySheet.FIRST_LINE, LibrarySheet.FIRST_LINE+96):
            cell_id = "{col}{row_iter}".format(col=column,row_iter=i)
            cell_value = str(self.library_sheet[cell_id].value)
            if(cell_value.isspace()):
                logger.warning(
                    'Cell {} contains empty spaces only. Remove content.'.format(cell_id)
                    )
                warning_empty_row += 1
            elif(self.library_sheet[cell_id].value != None):
                cellID_withSample.append(i)
            else:
                cellID_noSample.append(cell_id)
        return(cellID_withSample, warning_empty_row)

    def ProjectInfo(self, config):
        """
        Retrieves the project information from couchDB, checks that the project exists in
        couchDB and is unique. Returns the information and the full project plateID.
        """
        # access to the project in couchDB using information from the given config file
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
            sys.exit(1)
        # more than one project found
        elif len(prow) > 1:
            logger.error(
                'Project ID not unique, please check your entry for the PlateID, it should have the format'
                'PxxxxxPx, where x are numbers. If your Plate ID is correct, contact your project coordinator.'
                )
            sys.exit(1)
        else:
            # puts the Document of the identified project in a new variable "pdoc"
            self.Project_Information = db.get(prow[0].id)
            self.Project_Plate_ID = project_plate_ID[1]

    def validate_project_Name(self):
        """
        Prints the identified project name based on the user supplied Plate/Project ID for
        control purposes by the project coordinator. Further checks that the
        plate number is not already in couchDB.
        """
    #    print(self.pdoc)
        project_name_DB = self.Project_Information['project_name']
        samples = self.Project_Information['samples'].keys()
        plate ='P{}_{}'.format(self.Project_Plate_ID.split("P")[1],self.Project_Plate_ID.split("P")[2])
        found_plate = [s for s in samples if plate in s]
        warning_project_name = 0
        if(len(found_plate)>0):
            new_plate_no = int(self.Project_Plate_ID.split("P")[2])
            new_plate_no += 1
            new_plate_ID = 'P{}P{}'.format(self.Project_Plate_ID.split("P")[1], new_plate_no)
            logger.warning(
                'Plate number {} is already used. Please increase the plate number to {}.'.format(self.Project_Plate_ID, new_plate_ID))
            warning_project_name = 1
        return(warning_project_name)

    def validate_sequencing_setup(self, info, cell_id_length):
        """
        checks that the ordered cycle number fits the given the insert length
        OBS currently there is a 15% difference allowed before a warning is given,
        this is hard coded atm.
        """
        warning_cycles = 0
        cycle_no_string = info['details']['sequencing_setup']
        cycle_no_array = cycle_no_string.split("x")
        cycle_no = int(cycle_no_array[0])*int(cycle_no_array[1])
        insert_length = self.library_sheet[cell_id_length].value

        if((int(cycle_no)*1.15) < int(insert_length)):
            logger.warning('The cycle number ({} = {}) is lower than the insert length ({}bp) in cell {}.'\
            .format(cycle_no_string,cycle_no,insert_length, cell_id_length))
            warning_cycles += 1
        return(warning_cycles)

    def validate(self):
        """
        - identifies the samples in a pool
        - detects missing entry in pool column
        - initiates index check which is done for each pool independently
        """
        pool_warnings = []

        cell_rowid_sample, warning_eR_sample = self.getRows(LibrarySheet.SAMPLE_NAME_COL)
        cell_rowid_pool, warning_eR_pool_sample = self.getRows(LibrarySheet.POOL_NAME_SAMPLE_COL)
        cell_rowid_pool_info, warning_eR_pool = self.getRows(LibrarySheet.POOL_NAME_COL)

        #check for missing  for pool ID entries
        if(len(cell_rowid_sample) > len(cell_rowid_pool)):
            missing_pool_rowid_list = setdiff1d(cell_rowid_sample, cell_rowid_pool)
            for missing_pool_rowid in missing_pool_rowid_list:
                logger.error(
                    'Missing pool definition in {}{}'.format(LibrarySheet.POOL_NAME_SAMPLE_COL, missing_pool_rowid))
                sys.exit(1)

        #initiate check for sequencing setup and discrepancies between ordered numbers
        #of cycles and average read length
        warnings_cycle = []
        for row_nr in cell_rowid_pool_info:
            cell_id_mol = "{col}{row_nr}".format(col=LibrarySheet.MOLARITY_COL, row_nr=row_nr) # to work on later
            cell_id_length = "{col}{row_nr}".format(col=LibrarySheet.LENGTH_COL, row_nr=row_nr)
            cell_id_pool = "{col}{row_nr}".format(col=LibrarySheet.POOL_NAME_COL, row_nr=row_nr)
            validator = Validator(self.library_sheet,cell_id_mol) # molarity is currently not checked
            result_numeric, warnings_numeric = validator.validate_numeric()
            warnings_c = self.validate_sequencing_setup(self.Project_Information, cell_id_length)
            warnings_cycle.append(warnings_c)

        #retrieve all pool IDs defined, in order to later analyse by pool
        pool_values = []
        for row_nr in cell_rowid_sample:
            current_cell_id_pool ="{col}{row_nr}".format(col=LibrarySheet.POOL_NAME_SAMPLE_COL, row_nr=row_nr)
            current_cell_value_pool = self.library_sheet[current_cell_id_pool].value
            pool_values.append(current_cell_value_pool)

        # initiate check by pool for index issues
        #    - differences in index lengths
        #    - low diversity between Indexes
        #    - duplicated indixes
        #    - differences in length within dual Indexes
        #    - unbalanced pools
        poolIDs = list(dict.fromkeys(pool_values))
        i = 0
        current_pool_rows = []
        pools_with_warnings = []
        for pool in poolIDs:
            for nrow_nr in cell_rowid_sample:
                current_cell_id_pool ="{col}{row_nr}".format(col=LibrarySheet.POOL_NAME_SAMPLE_COL, row_nr=nrow_nr)
                current_cell_value_pool = self.library_sheet[current_cell_id_pool].value
                if(current_cell_value_pool == pool):
                    current_pool_rows.append(nrow_nr)
            validator = Validator(self.library_sheet)

            dictionary_sel_index = validator.select_index(current_pool_rows, pool)
            dictionary_val_index = \
            validator.validate_index(dictionary_sel_index["result_index"],
                                    pool,
                                    dictionary_sel_index["sindex"]

                                    )
            # summary of warnings
            all_warnings = [
                            dictionary_val_index["warning_low_div"],
                            dictionary_val_index["warning_index_length"],
                            dictionary_val_index["warning_index_balance"],
                            warnings_cycle[i],
                            dictionary_sel_index["warning_index_mix"],
                            dictionary_sel_index["warning_length_comp"]
                            ]
            sums_warnings = sum(all_warnings)
            if(sums_warnings > 0):
                pools_with_warnings.append(pool)
            current_pool_rows =[]
            i += 1

        return(len(pools_with_warnings), len(poolIDs))

class Validator(object):
    # Initializer / Instance attributes
    def __init__(self, access_sample_info_sheet, molarityID = None):
        self.access_sample_info_sheet = access_sample_info_sheet
        self.molarityID = molarityID

    # instance methods
    def validate_numeric(self):
        """Checks whether value is numeric or not."""
        warnings_numeric = 0
        for checkNumbers in [self.molarityID]:
            if not isinstance(self.access_sample_info_sheet[checkNumbers].value, numbers.Number):
                try:
                    float(self.access_sample_info_sheet[checkNumbers].value.replace(",", "."))
                    logger.error(
                        'Cell {} with value \"{}\" is not numeric due to decimal point/comma clash.'\
                        .format(self.access_sample_info_sheet[checkNumbers].coordinate, self.access_sample_info_sheet[checkNumbers].value)
                        )
                    warnings_numeric += 1
                    return False
                except ValueError:
                    logger.error(
                    'Cell {} with value {} is \"'.format(self.access_sample_info_sheet[checkNumbers].coordinate)+ self.access_sample_info_sheet[checkNumbers].value + '\" is not numeric'
                    )
                    warnings_numeric += 1
                except TypeError:
                    if self.access_sample_info_sheet[checkNumbers].value is None:
                        logger.error(
                        'Cell {} is numeric but empty'.format(self.access_sample_info_sheet[checkNumbers].coordinate)
                        )
                        warnings_numeric += 1
                        return False
                    else:
                        raise
            return(False, warnings_numeric)

    # currently not used
    #def validate_mol(self, min_mol, max_mol):
    #    warning_val_mol = 0
    #    if(self.access_sample_info_sheet[self.concentrationID].value < min_mol) \
    #    or (self.access_sample_info_sheet[self.concentrationID].value > max_mol):
    #        logger.warning('Sample molarity ({}ng/ul) in cell {} is out of specifications: {}-{}ng/ul'\
    #        .format(self.access_sample_info_sheet[self.molarityID].value,self.molarityID, min_mol, max_mol))
    #        warning_val_mol += 1
    #    return(True, warning_val_mol)

    def select_index(self, pool_rows, pool):
        '''
        - identifes whether pools contain standard indixes, custom indexes or both
        - will detect non-nucleotide letters
        - checks for double indexes in pools
        - detects missing Indexes
        - checks for minimal distance between Indexes
        - checks for similar index length (warning)
        - checks pool balance
        '''
        warning_mixed_indexes = 0
        warning_component_length = 0

        # retrieve the Cell IDs in for NGI standard indexes or custom indexes
        cell_id_standard_index = []
        cell_id_custom_index = []
        for row_nr in pool_rows:
           cell_id_standard_index.append("{col}{row_nr}".format(col  =LibrarySheet.SINDEX_COL, row_nr = row_nr))
           cell_id_custom_index.append("{col}{row_nr}".format(col = LibrarySheet.CINDEX_COL, row_nr = row_nr))

        # retrieves index sequences for NGI standard indexes
        sindex_list = []
        sindex_absent = []
        for sindex in cell_id_standard_index:
            if (self.access_sample_info_sheet[sindex].value is None):
                sindex_absent.append(sindex)
            elif(self.access_sample_info_sheet[sindex].value == "noIndex"):
                sindex_list.append(self.access_sample_info_sheet[sindex].value)
            else:
                index_only = re.split('\(|\)', self.access_sample_info_sheet[sindex].value)[1]

                # checks that dual indexes have the same length in both components
                split_index = re.split('[-_]',index_only)
                if(len(split_index) > 1):
                    if(len(split_index[0]) != len(split_index[1])):
                        logger.warning("Length of the two components in dual index {} and {} is different.".format(split_index[0], split_index[1]))
                        warning_component_length += 1

                # merges dual indexes to one sequence
                index_only = re.sub('[-_]', '', index_only)
                sindex_list.append(index_only)

                # generates error if both custom and NGI standard index are selected for the same sample
                if (self.access_sample_info_sheet["{col}{row_nr}".format(col=LibrarySheet.CINDEX_COL, row_nr=sindex.split(LibrarySheet.SINDEX_COL)[1])].value is not None):
                    logger.error('Custom and Standard Index selected for the sample in fields {}{} and {}{}. Please clarify which of the two indexes was used.'\
                    .format(LibrarySheet.SINDEX_COL, sindex.split(LibrarySheet.SINDEX_COL)[1], LibrarySheet.CINDEX_COL, sindex.split(LibrarySheet.SINDEX_COL)[1]))
                    sys.exit(1)

        # retrieves index sequences for custom indexes
        cindex_list = []
        cindex_absent = []
        for cindex in cell_id_custom_index:
            cindex_value = self.access_sample_info_sheet[cindex].value
            if (cindex_value is None):
                cindex_absent.append(cindex)
            else:
                # checks that dual indexes have the same length in both components
                split_cindex = re.split('[-_]',cindex_value)
                if(len(split_cindex) > 1):
                    if(len(split_cindex[0]) != len(split_cindex[1])):
                        logger.warning("Length of the two components in dual index {} and {} is different.".format(split_cindex[0], split_cindex[1]))
                        warning_component_length += 1

                # merges dual indexes to one sequence
                index_only = re.sub('[-]','', cindex_value)
                cindex_list.append(index_only)

        # identifies missing index specification for individual samples
        # and detects wether NGI standard indexes or custom indexes are chosen for a given pool
        error_missing_index = 0
        warning_mixed_indexes = 0
        sindex_chosen = True
        if(len(cindex_absent) == len(pool_rows)):
            sel_index = sindex_list
            if(len(sindex_list) != len(pool_rows)):
                for absent_index in sindex_absent:
                    logger.error("missing index in row {}".format(re.sub("\D","",absent_index)))
                    sys.exit(1)
        elif(len(sindex_absent) == len(pool_rows)):
            sel_index = cindex_list
            sindex_chosen = False
            if(len(cindex_list) != len(pool_rows)):
                for absent_index in cindex_absent:
                    logger.error("missing index in row {}".format(re.sub("\D","",absent_index)))
                    sys.exit(1)
        else:
            sindex_chosen = False

            # warning if a pool consists out of a mix of NGI standard indexes and custom indexes
            sel_index = sindex_list + cindex_list
            if(len(sel_index) != len(pool_rows)):
                rowID_cindex_absent = [re.sub("\D","",x) for x in cindex_absent]
                rowID_sindex_absent = [re.sub("\D","",x) for x in sindex_absent]

                missing_indexes = list(set(rowID_cindex_absent).intersection(rowID_sindex_absent))
                for mIndex in missing_indexes:
                    logger.error('missing index in row {}'.format(mIndex))

            logger.warning('mix between custom and standard indexes in pool {}.'\
            .format(pool))
            warning_mixed_indexes += 1

        dictionary_index = {
        "result_index" : sel_index,
        "sindex" : sindex_chosen,
        "warning_index_mix" : warning_mixed_indexes,
        "warning_length_comp" : warning_component_length
        }

        # returns warnings for check summary
        return(dictionary_index)

    def validate_index(self, index_seq, pool_name, sindex):
        '''
        does all the fancy index checks
        '''
        # allows for entry "noIndex" if only one sample is defined in the pool
        c = Counter(index_seq)
        if(c['noIndex'] > 0 and len(index_seq) != 1):
            logger.error('Pool {} contains undefined index(es) (\"noIndex\")'.format(pool_name))
            sys.exit(1)
        elif(c['noIndex'] == 1 and len(index_seq) == 1):
            logger.info('Pool {} containing one sample is not indexed.'.format(pool_name))
        elif(c['noIndex'] == 0):
            # checks that all indexes in a pool are unique
            for index, index_count in c.most_common():
                if(index_count>1):
                    logger.error('The index sequence \"{}\" in pool {} is not unique for this pool.'\
                    .format(index, pool_name))
                    sys.exit(1)
                else:
                    break

        warning_low_div = 0
        index_count = 1
        charRE = re.compile(r'[^ATCGNatcgn\-\.]')
        for index in index_seq:
            # checks that indexes only contain valid letters
            index_search = charRE.search(index)
            if(bool(index_search) and index != "noIndex"):
                logger.error('The index sequence \"{}\" in pool {} contains invalid characters.'\
                ' Allowed characters: A/T/C/G/N/a/t/c/g/n/-'
                .format(index, pool_name))
                sys.exit(1)

            # check that indexes within a pool have minimum diversity
            for i in range(index_count,len(index_seq)):
                levenshtein_distance = lev.distance(index.lower(), index_seq[i].lower())
                if levenshtein_distance < LibrarySheet.MAX_DISTANCE:
                    logger.warning('The index sequences {} and {} in pool {}'\
                    ' display low diversity (only {} nt difference).'\
                    .format(index,index_seq[i], pool_name, levenshtein_distance))
                    warning_low_div += 1
            index_count += 1

        # checks index length
        warning_index_length = 0
        warning_index_balance = 0
        index_length = []
        for index in index_seq:
            index_length.append(len(index))
        count_length = Counter(index_length)

        # checks color balance in the pool
        max_length = sorted(count_length.keys())[-1]
        min_length = sorted(count_length.keys())[0]
        index_list_colour = []
        for index in index_seq:
            index_colour = index.replace('T','G').replace('A','B').replace('C', 'R')
            index_list_colour.append(list(index_colour))
        for row_nr in range(0,max_length):
            column = []
            for row in index_list_colour:
                try:
                    column.append(row[row_nr])
                except IndexError:                      # exception for pools with different index length of samples
                    logger.warning('There are {} different index lengths in pool {}, please double check the sequences.'\
                    .format(len(count_length),pool_name))
                    warning_index_length += 1
            count_colour = Counter(column)
            if(len(count_colour)<2 and sum(count_colour.values()) > 1):
                logger.warning('Indexes in pool {} unbalanced at position {}'\
                .format(pool_name, row_nr+1))
                warning_index_balance += 1

        dictionary_val_index = {
        "warning_low_div" : warning_low_div,
        "warning_index_length" : warning_index_length,
        "warning_index_balance" : warning_index_balance
        }
        # returns warnings for check summary
        return(dictionary_val_index)


def main(input_sheet, config_statusDB):
    # Instantiate the LibrarySheet object
    sheetOI = LibrarySheet(input_sheet)
    # get Project Information from couchDB
    sheetOI.ProjectInfo(config_statusDB)
    # validate the project name to ensure correct identification in couchDB
    sheetOI.validate_project_Name()
    # validate all entries
    pool_fail, poolIDs = sheetOI.validate()
    # final check summary
    logger.info(
        'Library submission check complete. {}/{} pool(s) pass without warnings.'\
        .format((poolIDs-pool_fail), poolIDs))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('libraryInfoSheet',
                        help="Completed sample info sent to NGI by the user.")
    parser.add_argument('config_statusDB',
                        help="settings file in yaml format to access statusDB \
                        in the format \"couch_server: http://<username>:<password>@tools.scilifelab.se:5984\"")
    args = parser.parse_args()

    main(args.libraryInfoSheet,  args.config_statusDB)
