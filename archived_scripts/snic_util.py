#!/usr/bin/env python

description="A python wrapper for SNIC API utilities. It requires a config file with SNIC credentials."

import argparse
import datetime
import logging
import json
import os
import requests
import sys
import yaml
from collections import OrderedDict
from taca.utils.statusdb import StatusdbSession as sdb
from ngi_pipeline.database.classes import CharonSession, CharonError

# set logger object
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s', datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

# Expected config format
config_format = ("snic:\n"
                 "\tsnic_api_url: <api_base_url>\n"
                 "\tsnic_api_user: <api_user_name>\n"
                 "\tsnic_api_password: <api_user_password>\n")

def to_bool(s):
    try:
        return {"yes":True, "no":False}[s]
    except KeyError:
        raise argparse.ArgumentTypeError('Only "yes/no" is allowed')

def proceed_or_not(question):
    sys.stdout.write("{}".format(question))
    while True:
        if sys.version_info[0] == 3:
            choice = input().lower()
        elif sys.version_info[0] == 2:
            choice = raw_input().lower()
        if choice in ['yes','y']:
            return True
        elif choice in ['no','n']:
            return False
        else:
            sys.stdout.write("Please respond with 'yes/y' or 'no/n'")


class snic_util(object):
    """ A class with SNIC utilities """
    def __init__(self, config=None, params=None):
        """ Instantiate the object with config properties """
        try:
            self.api_url = config['snic_api_url'].rstrip('/')
            self.api_user = config['snic_api_user']
            self.api_pass = config['snic_api_password']
            self.api_cred = (self.api_user, self.api_pass)
        except KeyError as e:
            print("Config is missing key {}".format(e))
            raise e
        # if any params given set them as attributes
        if params:
            for _key, _val in params.items():
                setattr(self, _key, _val)

    def create_grus_project(self, proj_data={}):
        """Create a GRUS delivery project with given info and return info of created project in JSON"""
        pdata = proj_data or getattr(self, 'proj_data', {})
        create_proj_url = "{}/ngi_delivery/project/create/".format(self.api_url)
        response = requests.post(create_proj_url, data=json.dumps(pdata), auth=self.api_cred)
        self._assert_response(response)
        return response.json()

    def update_grus_project(self, prj_snic_id=None, updata={}):
        """Update a GRUS delivery project with given info and return info of updated project in JSON"""
        psnic = prj_snic_id or getattr(self, 'prj_snic_id', None)
        udata = updata or getattr(self, 'updata', {})
        update_proj_url = "{}/ngi_delivery/project/{}/update/".format(self.api_url, psnic)
        response = requests.post(update_proj_url, data=json.dumps(udata), auth=self.api_cred)
        self._assert_response(response)
        return response.json()

    def get_user_info(self, user_email=None):
        """Search for user in SNIC and return list with matching hits. Each hits is in JSON format"""
        uemail = user_email or getattr(self, 'user_email')
        search_info = {"search_url": "{}/person/search/".format(self.api_url),
                       "search_params": {"email_i": uemail}}
        user_hits = self._search_snic(**search_info)
        if len(user_hits) == 0:
            logger.info("No user found with email {}".format(uemail))
        return user_hits

    def get_project_info(self, grus_project=None):
        """Search for delivery project in SNIC and return list with matching hits. Each hits is in JSON format"""
        gproject = grus_project or getattr(self, 'grus_project')
        search_info = {"search_url": "{}/project/search/".format(self.api_url),
                       "search_params": {"name": gproject}}
        project_hits = self._search_snic(**search_info)
        if len(project_hits) == 0:
            logger.info("No projects was found with name {}".format(gproject))
        return project_hits

    def _search_snic(self, search_url, search_params):
        response = requests.get(search_url, params=search_params, auth=self.api_cred)
        self._assert_response(response)
        return response.json()["matches"]

    def _assert_response(self, response):
        assert response.status_code == 200, "Failed connecting {} via SNIC API".format(response.url)


class _snic_wrapper(snic_util):
    """A wrapper class that makes use of SNIC UTIL class and help out for relavant calls"""
    def __init__(self, config=None, params=None, execute_mode=False):
        super(_snic_wrapper, self).__init__(config, params)
        if execute_mode:
            getattr(self, "_{}".format(self.mode))()

    def _create_project(self):
        try:
            pi_snic_id = self.get_user_info(user_email=self.pi_email)[0]['id']
            logger.info("For PI email '{}' found SNIC ID '{}'".format(self.pi_email, pi_snic_id))
        except IndexError as e:
            logger.error("Could not find PI email '{}' in SNIC. So can not create a delivery project with that PI".format(self.pi_email))
            raise SystemExit
        supr_date_format = '%Y-%m-%d'
        today = datetime.date.today()
        endday = today + datetime.timedelta(days=self.days)
        mem_snic_ids = []
        if self.members:
            for mem in self.members:
                try:
                    mem_snic_id = self.get_user_info(user_email=mem)[0]['id']
                    mem_snic_ids.append(mem_snic_id)
                    logger.info("For email '{}' found SNIC ID '{}'".format(mem, mem_snic_id))
                except IndexError as e:
                    logger.error("Could not find email '{}' in SNIC. So can not add to delivery project".format(mem))
        prj_data = {'ngi_project_name': self.project,
                    'title': self.title or "DELIVERY_{}_{}".format(self.project, today.strftime(supr_date_format)),
                    'pi_id': pi_snic_id,
                    'start_date': today.strftime(supr_date_format),
                    'end_date': endday.strftime(supr_date_format),
                    'ngi_ready': False,
                    'ngi_sensitive_data': self.sensitive,
                    'member_ids': mem_snic_ids}
        question = ("\nA GRUS delivery project will be created with following information, check and confirm\n\n{}\n\n"
                    "NOTE: Sensivity for project is my default set to 'True', it can be change dby calling '--no-sensitive'. "
                    "Also parameters '--title / --members / --days' can be used to control the defaults, check help\n\n"
                    "So proceed with the project creation (yes/no) ? ".format(json.dumps(prj_data, indent=4)))
        if proceed_or_not(question):
            logger.info("Creating GRUS delivery project")
            grus_proj_details = self.create_grus_project(prj_data)
            logger.info("Created GRUS delivery project with id '{}'".format(grus_proj_details["name"]))
            #Post to statusdb and Charon
            if self.update_databases:
                dbSess=DbConnections()
                dbSess.add_delivery_proj_in_charon(grus_proj_details["name"], self.project)
                dbSess.add_delivery_proj_in_statusdb(grus_proj_details["name"], self.project)
        else:
            logger.warning("Project will not be created. EXITING")

    def _extend_project(self):
        ukey = "end_date"
        endday = datetime.date.today() + datetime.timedelta(days=self.days)
        uval = endday.strftime('%Y-%m-%d')
        self._execute_project_update(ukey=ukey, uval=uval)

    def _change_pi(self):
        ukey = "pi_id"
        try:
            pi_snic_id = self.get_user_info(user_email=self.pi_email)[0]['id']
            logger.info("For PI email '{}' found SNIC ID '{}'".format(self.pi_email, pi_snic_id))
        except IndexError as e:
            logger.error("Could not find PI email '{}' in SNIC. So can not new PI for {}".format(self.pi_email, self.grus_project))
            raise SystemExit
        uval = pi_snic_id
        self._execute_project_update(ukey=ukey, uval=uval)

    def _change_sensitive(self):
        ukey = "ngi_sensitive_data"
        uval = self.sensitive
        self._execute_project_update(ukey=ukey, uval=uval)

    def _project_info(self):
        interested_keys = ["name", "id", "title", "ngi_project_name", "pi", "members", "ngi_sensitive_data", "start_date", "end_date"]
        self._execute_search(exec_func=self.get_project_info, filter_keys=interested_keys)

    def _user_info(self):
        interested_keys = ["first_name", "last_name", "id", "email", "department", "organization"]
        self._execute_search(exec_func=self.get_user_info, filter_keys=interested_keys)

    def _execute_project_update(self, ukey, uval):
        try:
            prj_info = self.get_project_info(grus_project=self.grus_project)[0]
            prj_snic_id = prj_info['id']
        except IndexError as e:
            logger.error("Project with name '{}' does not exist in SNIC".format(self.grus_project))
            raise SystemExit
        oval = prj_info.get('pi', {}).get('id') if ukey == 'pi_id' else prj_info.get(ukey)
        question = "\nProject {}: old value for key '{}' is '{}'. Replace it with '{}' (yes/no) ? ".format(self.grus_project, ukey, oval, uval)
        if proceed_or_not(question):
            logger.info("Updating project {}".format(self.grus_project))
            updated_info = self.update_grus_project(prj_snic_id=prj_snic_id, updata={ukey:uval})
            logger.info("Updated project {}".format(self.grus_project))
        else:
            logger.warning("Project '{}' will not be updated. EXITING".format(self.grus_project))

    def _execute_search(self, exec_func, filter_keys=[], all_info=False):
        all_info = all_info or getattr(self, "all_info", False)
        search_hits = exec_func()
        for ind, inf in enumerate(search_hits, 1):
            oinf = inf if all_info else OrderedDict((k, inf.get(k)) for k in filter_keys)
            print("Hit {}:\n{}".format(ind, json.dumps(oinf, indent=4)))

class DbConnections():
    def __init__(self):
        with open(os.getenv('STATUS_DB_CONFIG'), 'r') as db_cred_file:
            db_conf = yaml.load(db_cred_file, Loader=yaml.SafeLoader)['statusdb']
        self.statusdbSess = sdb(db_conf, db="projects")
        self.CharonSess = CharonSession()

    def add_delivery_proj_in_charon(self, delivery_proj, projectid):
        '''Updates delivery_projects in Charon at project level
        '''
        try:
            #fetch the project
            project_charon = self.CharonSess.project_get(projectid)
            delivery_projects = project_charon['delivery_projects']
            if delivery_proj not in delivery_projects:
                delivery_projects.append(delivery_proj)
                self.CharonSess.project_update(projectid, delivery_projects=delivery_projects)
                logger.info('Charon delivery_projects for project {} updated with value {}'.format(projectid, delivery_proj))
            else:
                logger.warn('Charon delivery_projects for project {} not updated with value {} because the value was already present'.format(projectid, delivery_proj))
        except Exception as e:
            logger.error('Failed to update delivery_projects in charon for {}. Error says: {}'.format(projectid, e))
            logger.exception(e)

    def add_delivery_proj_in_statusdb(self, delivery_proj, projectid):
        '''Updates delivery_projects in StatusDB at project level
        '''
        project_page=self.statusdbSess.get_project(projectid)
        dprojs = project_page.get('delivery_projects', [])
        dprojs.append(delivery_proj)
        project_page['delivery_projects'] = dprojs
        try:
            self.statusdbSess.save_db_doc(project_page)
            logger.info('Delivery_projects for project {} updated with value {} in statusdb'.format(projectid, delivery_proj))
        except Exception as e:
            logger.error('Failed to update delivery_projects in statusdb for {}. Error says: {}'.format(projectid, e))
            logger.exception(e)

if __name__ == "__main__":
    # add main parser object with global parameters
    parser = argparse.ArgumentParser(prog=__file__, description="SNIC utility tools")
    parser.add_argument("-c", "--config", type=argparse.FileType('r'), metavar="",
                        default=os.getenv('SNIC_API_STOCKHOLM'), help="Config with SNIC credentials")
    subparser = parser.add_subparsers(title="subcommands", dest="mode", metavar="MODE", help="Available SNIC utility modes")
    # add sub command for creating GRUS project
    subparser_create_project = subparser.add_parser("create_project", help="Create a GRUS delivery project with given information")
    subparser_create_project.add_argument("-p", "--project", required=True, type=str, help="NGI project name/id")
    subparser_create_project.add_argument("-e", "--pi-email", required=True, type=str, help="PI email address")
    subparser_create_project.add_argument("-s", "--sensitive", required=True, type=to_bool,
                                          help="Choose if the project is sensitive or not, (Only 'yes/no' is allowed)")
    subparser_create_project.add_argument("-d", "--days", type=int, default=90, help="Number of days created GRUS delivery project to be active")
    subparser_create_project.add_argument("-t", "--title", type=str, help="Custom title for GRUS delivery project")
    subparser_create_project.add_argument("-m", "--members", type=str, action="append", help="Members to be added in GRUS delivery project")
    subparser_create_project.add_argument("--update-databases", action="store_true", help="Use to store generated delivery project in Charon and statusdb")
    # add sub command for extending a project
    subparser_extend_project = subparser.add_parser("extend_project", help="Extend the end date of GRUS project for given 'days'")
    subparser_extend_project.add_argument("-g", "--grus-project", required=True, type=str, help="Grus project id, format should be 'deliveryNNNNN'")
    subparser_extend_project.add_argument("-d", "--days", required=True, type=int, help="Number of days to extend a GRUS delivery project")
    # add sub command to search a project
    subparser_project_info = subparser.add_parser("project_info", help="Get information for specified GRUS project")
    subparser_project_info.add_argument("-g", "--grus-project", required=True, type=str, help="Grus project id, format should be 'deliveryNNNNN'")
    subparser_project_info.add_argument("-a", "--all-info", action="store_true", help="Display all information without default filtering")
    # add sub command to sear a user
    subparser_user_info = subparser.add_parser("user_info", help="Get SNIC information for specified user")
    subparser_user_info.add_argument("-u", "--user-email", required=True, type=str, help="User email address to fetch their SNIC details")
    subparser_user_info.add_argument("-a", "--all-info", action="store_true", help="Display all information without default filtering")
    # add sub command to change a PI for a project
    subparser_change_pi = subparser.add_parser("change_pi", help="Change PI of mentioned GRUS project to given PI")
    subparser_change_pi.add_argument("-g", "--grus-project", required=True, type=str, help="Grus project id, format should be 'deliveryNNNNN'")
    subparser_change_pi.add_argument("-e", "--pi-email",required=True, type=str, help="Email address of user to set as new PI")
    # add sub command to change sensivity for a project
    subparser_change_sensitive = subparser.add_parser("change_sensitive", help="Change sensitivity of GRUS project")
    subparser_change_sensitive.add_argument("-g", "--grus-project", required=True, type=str, help="Grus project id, format should be 'deliveryNNNNN'")
    subparser_change_sensitive.add_argument("-s", "--sensitive", required=True, type=to_bool,
                                          help="Choose if the project is sensitive or not, (Only 'yes/no' is allowed)")

    params = vars(parser.parse_args())
    # try loading config file
    try:
        snic_config = yaml.load(params["config"], Loader=yaml.SafeLoader)["snic"]
    except:
        logger.error("Error loading config file, make sure config is in following format\n\n{}".format(config_format))
        raise
    _snic_wrapper(config=snic_config, params=params, execute_mode=True)
