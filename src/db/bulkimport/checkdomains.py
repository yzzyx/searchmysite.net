import urllib.request
from urllib.error import URLError, HTTPError
from http.client import InvalidURL
import tldextract
import json
import sys
import psycopg2
import psycopg2.extras
import os

if len(sys.argv) > 1:
    inp = sys.argv[1]
else:
    exit("You need to provide an input, e.g. sitelist1 will load from data/sitelist1.txt and save to data/sitelist1.json")

# Get database password from ../../.env
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
if not POSTGRES_PASSWORD:
    with open('../../.env') as f:
        for line in f:
            if '=' in line and not line.startswith('#'):
                key, value = line.strip().split('=')
                if key == 'POSTGRES_PASSWORD': POSTGRES_PASSWORD = value

# Input file name.
# Can contain domains in domain.com or http://domain.com format, with comments starting #
#input_file = "data/test.txt"
#input_file = "data/domains_elders.txt"
#input_file = "data/domains_wiki_users.txt"
#input_file = "data/chatnames.txt"
input_file = "data/" + inp + ".txt"
print("Input file: {}".format(input_file))

# Whether the contents of the input file have been reviewed, 
# i.e. if True they can be inserted direct to tblIndexedDomains or if False
#input_reviewed = False
input_reviewed = True

# Which database to read to check if the domains already exist in the database
#database_host = "db" # Dev database
database_host = "searchmysite.net" # Prod database

# SQL
sql_select_indexed = 'SELECT * FROM tblIndexedDomains WHERE domain = (%s);'
sql_select_pending = 'SELECT * FROM tblPendingDomains WHERE domain = (%s);'
sql_select_excluded = 'SELECT * FROM tblExcludeDomains WHERE domain = (%s);'

# JSON file where the output will be saved
output_file = "data/" + inp + ".json"
print("Output file: {}".format(output_file))

# Output will be a list of dicts, where each dict has the following keys:
# domain, e.g. "aaronparecki.com"
# home_page, e.g. "https://aaronparecki.com/"
# source, e.g. "domains_elders.txt"
# valid, e.g. True, i.e. whether the home_page returns a 200 response
# reviewed, e.g. False, i.e. whether manually reviewed to confirm whether a personal website or not
# already_in_list, e.g. False, i.e. whether the domains has been encountered in the input file already
# message, e.g. "Success" if valid is True and already_in_list and already_in_database are False, otherwise error message
# already_in_database, e.g. True, i.e. whether not already in the database 

# Once completed for all the input_files, the insertdomains.py script can be run

# For each domain:
# 1. Find out the home page, e.g. add http:// or https:// if necessary, and
#    check if the home_page is still valid, i.e. returns a 200 response.
# 2. Check it isn't already in the database, 
#    either in tblIndexedDomains or tblPendingDomains or tblExcludeDomains
# Note: if running on local dev need to turn off https://my.virginmedia.com/advancederrorsearch/ 
# to prevent non-existent domains getting a 200 response

def check_domains():
    print("input_domain, input_home, output_domain, output_home, valid, response_code, response, already_in_list, already_in_database")
    headers = {'Accept': 'text/html', 'User-Agent': 'Python'} # Some sites give a 403 Forbidden if they don't get headers
    f = open(input_file, 'r')
    lines = f.readlines()
    for line in lines:
        line = line.strip()
        if line != "" and not line.startswith("#"):
            result = {}

            # Step 1: Get input domain and home_page
            if line.startswith("http"):
                domain = extract_domain(line)
                home_page = line
            else:
                domain = line.lower()
                home_page = "http://" + domain + "/"
            print('{}, {}, '.format(domain, home_page), end='')

            # Step 2: Get response
            try:
                req = urllib.request.Request(home_page, method="HEAD", headers=headers)
                resp = urllib.request.urlopen(req)
                home_page = resp.url # Going to get the home page from the response in case there have been redirects
                domain = extract_domain(home_page)
                response_code = resp.getcode()
                if response_code == 200:
                    valid = True
                    message = "Success"
                else:
                    valid = False
                    message = "Unknown response code {}".format(response_code)
            except HTTPError as err:
                if err.reason == "Permanent Redirect": # Some sites return a 308 Permanent Redirect for an http request to be redirected to https but don't provide the Location header so this throws an HTTPError
                    try:
                        home_page = "https://" + domain + "/"
                        req = urllib.request.Request(home_page, method="HEAD", headers=headers)
                        resp = urllib.request.urlopen(req)
                        domain = extract_domain(home_page)
                        response_code = resp.getcode()
                        if response_code == 200:
                            valid = True
                            message = "Success"
                    except:
                            valid = False
                            response_code = "-"
                            message = "Error {}".format(response_code)
                else:
                    valid = False
                    response_code = err.code
                    message = "HTTPError ({})".format(err.reason)
            except InvalidURL as err:
                valid = False
                response_code = resp.getcode()
                message = "InvalidURL ({})".format(err)
            except URLError as err:
                valid = False
                response_code = resp.getcode()
                message = "URLError ({})".format(err.reason)
            except:
                valid = False
                response_code = resp.getcode()
                message = "Other error ({})".format(sys.exc_info())
            print("{}, {}, {}, {}, {}, ".format(domain, home_page, valid, response_code, message), end='')
            result['domain'] = domain
            result['home_page'] = home_page
            result['source'] = input_file
            result['valid'] = valid
            result['reviewed'] = input_reviewed
            result['message'] = message

            # Step 3: See if it is a duplicate entry in the input file
            if valid:
                already_in_list = False
                for d in domains:
                    if d['domain'] == domain:
                        already_in_list = True
                print("{}, ".format(already_in_list), end='')
                result['already_in_list'] = already_in_list

            # Step 4: See if it is already in the database
            if valid:
                already_in_database = False
                conn = psycopg2.connect(host=database_host, dbname="searchmysitedb", user="postgres", password=POSTGRES_PASSWORD)
                cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                cursor.execute(sql_select_indexed, (domain,))
                indexed_result = cursor.fetchone()
                cursor.execute(sql_select_pending, (domain,))
                pending_result = cursor.fetchone()
                cursor.execute(sql_select_excluded, (domain,))
                excluded_result = cursor.fetchone()
                if indexed_result is not None or pending_result is not None or excluded_result is not None:
                    already_in_database = True
                print("{}, ".format(already_in_database), end='')
                result['already_in_database'] = already_in_database

            print("")
            domains.append(result)

    f.close()

# This is copied from ../../web/content/dynamic/searchmysite/util.py - if that is updated this should be too
# Only conn line is changed
domains_allowing_subdomains_sql = "SELECT setting_value FROM tblSettings WHERE setting_name = 'domain_allowing_subdomains';"
def extract_domain(url):
    # Get the domain from the URL
    if not url: url =""
    tld = tldextract.extract(url) # returns [subdomain, domain, suffix]
    domain = '.'.join(tld[1:]) if tld[2] != '' else tld[1] # if suffix empty, e.g. localhost, just use domain
    domain = domain.lower() # lowercase the domain to help prevent duplicates
    # Look up list of domains which allow subdomains from database
    domains_allowing_subdomains = []
#    conn = get_db()
    conn = psycopg2.connect(host=database_host, dbname="searchmysitedb", user="postgres", password=POSTGRES_PASSWORD)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute(domains_allowing_subdomains_sql)
    results = cursor.fetchall()
    for result in results:
        domains_allowing_subdomains.append(result['setting_value'])
    # Add subdomain if in domains_allowing_subdomains
    if domain in domains_allowing_subdomains: # special domains where a site can be on a subdomain
        if tld[0] and tld[0] != "":
            domain = tld[0] + "." + domain
    return domain

domains = []

check_domains()

json = json.dumps(domains, sort_keys=True, indent=4)
f = open(output_file,"w")
f.write(json)
f.close()
