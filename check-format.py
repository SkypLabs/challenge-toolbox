#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# -*- mode: python; -*-
import json
import logging
import os
import re
import subprocess
import sys
from glob import glob as glob

from common import get_sys_args, read_config
from common import init_logger

CAPABILITIES = {  # all linux capabilities:
                   'SETPCAP', 'SYS_MODULE', 'SYS_RAWIO', 'SYS_PACCT', 'SYS_ADMIN', 'SYS_NICE',
                   'SYS_RESOURCE', 'SYS_TIME', 'SYS_TTY_CONFIG', 'MKNOD', 'AUDIT_WRITE',
                   'AUDIT_CONTROL', 'MAC_OVERRIDE', 'MAC_ADMIN', 'NET_ADMIN', 'SYSLOG', 'CHOWN',
                   'NET_RAW', 'DAC_OVERRIDE', 'FOWNER', 'DAC_READ_SEARCH', 'FSETID', 'KILL',
                   'SETGID', 'SETUID', 'LINUX_IMMUTABLE', 'NET_BIND_SERVICE', 'NET_BROADCAST',
                   'IPC_LOCK', 'IPC_OWNER', 'SYS_CHROOT', 'SYS_PTRACE', 'SYS_BOOT', 'LEASE',
                   'SETFCAP', 'WAKE_ALARM', 'BLOCK_SUSPEND',
               } - {  # blacklisted capabilities (subject to change):
                   'MAC_ADMIN', 'MAC_OVERRIDE', 'SYS_ADMIN', 'SYS_MODULE', 'SYS_RESOURCE',
                   'LINUX_IMMUTABLE', 'SYS_BOOT', 'BLOCK_SUSPEND', 'WAKE_ALARM',
               }

PORT_RANGE = {'min': 1, 'max': 65535}

CONTROLLER_PROTOCOL = 'controller'
PROTOCOLS = {'udp', 'tcp', 'ssh', 'http', 'ws', CONTROLLER_PROTOCOL}

DIFFICULTY_RANGE = {'min': 10, 'max': 500}
NAME_RANGE = {'min': 3, 'max': 200}
SUMMARY_RANGE = {'min': 30, 'max': 200}
DESCRIPTION_RANGE = {'min': 100, 'max': 30000}

CONFIG_KEYS = {'version', 'crp_config', 'difficulty', 'enable_flag_input', 'flag', 'name', 'skills', 'recommendations',
               'owners'}

COST_SUM = 90
MIN_WRITEUP_SECTIONS = 3

IP = '127.0.0.1'
CONTROLLER_PORT = 5555


def check_config(config, is_static):
    invalid_keys = set(config.keys()) - set(CONFIG_KEYS)
    if len(invalid_keys) > 0:
        logging.error('Invalid key(s) found in config.yml: %s' % invalid_keys)

    if config['version'][:1] != 'v':
        logging.error('Invalid version. The version number must start with the letter v')
    elif config['version'] == 'v1':
        logging.error('This version is deprecated, please use v2.0.0')
    elif config['version'] != 'v2.0.0':
        logging.error('Invalid version. The supplied config version is not supported')

    # Difficulty
    try:
        assert int(config['difficulty']) in range(DIFFICULTY_RANGE['min'], DIFFICULTY_RANGE['max'] + 1)
    except Exception:
        logging.error('Invalid difficulty in config.yml. '
                      'Valid values: %d - %d' % (DIFFICULTY_RANGE['min'], DIFFICULTY_RANGE['max']))

    # Name
    try:
        assert len(config['name']) in range(NAME_RANGE['min'], NAME_RANGE['max'] + 1)
    except Exception:
        logging.error('Invalid challenge name in config.yml. '
                      'Name should be a string with length %d - %d' % (NAME_RANGE['min'], NAME_RANGE['max']))

    # Skills
    if not isinstance(config['skills'], list):
        logging.error('Invalid skills in config.yml. Skills should be placed into a list.\n'
                      '\tValid skills are listed here: \n'
                      '\thttps://platform.avatao.com/api-explorer/#/api/core/skills/')

    # Recommendations
    if not isinstance(config['recommendations'], dict):
        logging.error('Invalid recommendations in config.yml. Recommendations should be added in the following '
                      'format:\n\n'
                      'recommendations:\n'
                      '\twww.example.com: \'Example webpage\'\n'
                      '\thttp://www.example2.com: \'Example2 webpage\''
                      '\thttp://example3.com: \'Example3 webpage\'')

    url_re = re.compile(r'(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)'
                        r'(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]'
                        r'+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:\'".,<>?«»“”‘’]))')

    for item in config['recommendations'].items():
        if url_re.fullmatch(item[0]) is None:
            logging.error('Invalid recommended URL (%s) found in config.yml' % item[0])

        if not isinstance(item[1], str):
            logging.error('The name of recommended url (%s) should be a string in config.yml' % item[1])

    # Owners
    if not isinstance(config['owners'], list):
        logging.error('Challenge owners (%s) should be placed into a list in config.yml' % config['owners'])

    email_re = re.compile(r"(^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$)")
    for owner in config.get('owners', []):
        if email_re.fullmatch(owner) is None:
            logging.error('Invalid owner email (%s) found in config.yml. Make sure you list the email addresses of the owners' % owner)

    if not is_static:
        controller_found = False
        for item in config['crp_config'].values():

            if not isinstance(item, dict):
                logging.error('Items of crp_config must be dictionaries.')

            if 'image' in item and item['image'].find('/') < 0:
                logging.error('If the image is explicitly defined, it must be relative '
                              'to the registry - e.g. challenge:solvable.')

            if 'capabilities' in item:
                invalid_caps = set(item['capabilities']) - CAPABILITIES
                if len(invalid_caps) > 0:
                    logging.error('Invalid capabilities: %s. Valid values: %s', invalid_caps, CAPABILITIES)

            if 'mem_limit' in item:

                if not isinstance(item['mem_limit'], str):
                    logging.error('Invalid mem_limit value: %s, The mem_limit should be a string like: 100M')

                if item['mem_limit'][-1] not in "M":
                    logging.error('Invalid mem_limit value: %s, The mem_limit should be a string ending with M'
                                  '(megabytes) no other unit is allowed')
                try:
                    mem_limit_number_part = int(item['mem_limit'][:-1])
                    if mem_limit_number_part > 999:
                        logging.error('Invalid mem_limit value: %s, The mem_limit can not be greater'
                                      ' than 999M')
                except Exception:
                    logging.error('Invalid mem_limit value: %s, mem_limit must start with a number and end with '
                                  'M (megabytes) no other unit is allowed')

            for port in item.get('ports', []):
                try:
                    port, protocol = port.split('/', 1)
                    try:
                        port = int(port)
                    except Exception:
                        logging.error('Invalid port. The port should be a number between 1 and 65535')

                    if PORT_RANGE['min'] > port or PORT_RANGE['max'] < port:
                        logging.error('Invalid port. The port should be a number between 1 and 65535')

                    if protocol not in PROTOCOLS:
                        logging.error('Invalid protocol in config.yml (crp_config): %s. Valid values: %s', protocol,
                                      PROTOCOLS)

                    elif protocol == CONTROLLER_PROTOCOL:
                        controller_found = True

                except Exception:
                    logging.error('Invalid port format. [port/protocol]')

        if not config.get('flag') and not controller_found:
            logging.error('Missing controller port [5555/%s] for a dynamic challenge.' % CONTROLLER_PROTOCOL)

    if str(config.get('enable_flag_input')).lower() not in ('true', 'false', '1', '0'):
        logging.error('Invalid enable_flag_input. Should be a boolean.')

    if is_static:
        try:
            assert isinstance(config['flag'], str)
        except AssertionError:
            logging.error('Invalid flag. Should be a string.')
        except KeyError:
            logging.error('Missing flag. Static challenges must have a static flag set.')


def check_dockerfile(filename):
    repo_pattern = 'FROM (docker\.io\/)?avatao\/'

    try:
        with open(filename, 'r') as f:
            d = f.read()
            if re.search(repo_pattern, d) is None:
                logging.error('Please use avatao base images for your challenges. Our base images'
                              ' are available at https://hub.docker.com/u/avatao/')
    except FileNotFoundError as e:
        logging.error('Could not open %s' % e.filename)

    except Exception as e:
        logging.error('An error occurred while loading %s. \n\tDetails: %s' % (filename, e))


def check_controller():
    check_dockerfile('controller/Dockerfile')
    static_flag = None

    try:
        static_flag = read_config()['flag']

    except KeyError:
        logging.info('(This is not an error) Flag is missing from the config file \n\t'
                     'You should implement a dynamic solution checker (e.g., with random flags, unit tests)'
                     'in the controller.')

    except FileNotFoundError as e:
        logging.error('Could not open %s' % e.filename)

    try:
        with open('controller/opt/server.py') as server_file:
            server = server_file.read()

    except FileNotFoundError as e:
        logging.error('Could not open %s' % e.filename)

    except Exception as e:
        logging.error('An error occurred while checking controller/opt/server.py. \n\tDetails: %s' % e)

    else:
        # Invoke test endpoint if not static
        _http_request('http://%s:%d/secret/test' % (IP, CONTROLLER_PORT))

        # Check controller's solution_check endpoint.
        solution_check_pattern = 'def solution_check\(\):'
        solution_check = re.findall(solution_check_pattern, server)

        if not (solution_check or static_flag):
            logging.error('Function "solution_check()" is missing from controller/opt/server.py. '
                          '\n\tPlease implement it and check user solutions dynamically (e.g., random flag '
                          'checking)\n\tor insert a static flag into config.yml.')


def check_misc():
    if not len(glob('src/*')):
        logging.warning('Missing or empty "src" directory. Please place your source files there '
                        'if your challenge has any. ')

    if not len(glob('README.md')):
        logging.warning('No README.md file is found. Readmes help others to understand your challenge.')

    if not len(glob('LICENSE')):
        logging.warning('No LICENSE file is found. Please add the (original) license file if you copied'
                        '\n\t  a part of your challenge from a licensed challenge.')

    if not len(glob('CHANGELOG')):
        logging.warning('No CHANGELOG file is found. If you modified an existing licensed challenge, please summarize '
                        '\n\t  what your changes were.')


def check_yml(filename, is_static: bool = False):
    try:
        config = read_config(filename)
        check_config(config, is_static)

    except FileNotFoundError as e:
        logging.error('Could not open %s' % e.filename)

    except KeyError as e:
        logging.error('Key "%s" is missing from %s' % (e.args[0], filename))

    except Exception as e:
        logging.error('An error occurred while loading %s. \n\tDetails: %s' % (filename, e))


def check_metadata():
    # Check if metadata files exist
    try:
        with open('metadata/description.md', 'r', encoding='utf-8') as description_file:
            _check_description(description_file)

        with open('metadata/summary.md', 'r', encoding='utf-8') as summary_file:
            s = summary_file.read()
            if len(s) not in range(SUMMARY_RANGE['min'], SUMMARY_RANGE['max'] + 1):
                logging.error('Summary should be minimally %d, maximally %d characters long.',
                              SUMMARY_RANGE['min'], SUMMARY_RANGE['max'])

        with open('metadata/writeup.md', 'r', encoding='utf-8') as writeup_file:
            _check_writeup(writeup_file)

    except FileNotFoundError as e:
        logging.error('Missing %s from metadata.' % e.filename)
        sys.exit(1)

    except UnicodeDecodeError as e:
        logging.error('Could decode markdown text: \n\n%s \n\tDetails: %s' % (e.object, e))

    except Exception as e:
        logging.error('Could not read file in metadata. \n\tDetails: %s' % e)


def sanity_check():
    # Check if the challenge is static
    is_static = (len(glob('solvable*/Dockerfile')) + len(glob('controller*/Dockerfile')) == 0)
    if is_static and len(glob('downloads/*')) == 0:
        logging.error('Static challenges should have a "downloads" directory for sharing challenge files with users.')

    check_yml('config.yml', is_static)

    if not is_static:
        check_controller()
        check_dockerfile('solvable/Dockerfile')

    check_metadata()
    check_misc()


def _check_description(description_file):
    description = description_file.read()
    if len(description) not in range(DESCRIPTION_RANGE['min'], DESCRIPTION_RANGE['max'] + 1):
        logging.error('Description should be minimum %d, maximum %d characters long.',
                      DESCRIPTION_RANGE['min'], DESCRIPTION_RANGE['max'])

    section_too_long_pattern = re.compile(r'^ {0,3}#{1,6} +.{151,}$', re.MULTILINE)
    section_too_short_pattern = re.compile(r'^ {0,3}#{1,6} +.{0,4}$', re.MULTILINE)

    section_long = re.findall(section_too_long_pattern, description)
    section_short = re.findall(section_too_short_pattern, description)

    if section_short:
        logging.error('Sections must be between 5 and 150 characters'
                      '\n\tDetails: %s' % section_short)
    if section_long:
        logging.error('Sections must be between 5 and 150 characters'
                     '\n\tDetails: %s' % section_long)

    section_not_capitalized_pattern = re.compile(r'^ {0,3}#{1,6} +[^A-Z].+.*$', re.MULTILINE)

    section_not_capitalized = re.findall(section_not_capitalized_pattern, description)

    if section_not_capitalized:
        logging.error('Sections must start with a capitalized letter'
                     '\n\tDetails: %s' % section_not_capitalized)

    # Description can contain only contain H4- or H5-style sections.
    maxh3_pattern = re.compile(r'^ {0,3}#{1,3} +.*$', re.MULTILINE)
    maxh3 = re.findall(maxh3_pattern, description)
    if maxh3:
        logging.error('Section name font size is too large in description.md. '
                      'Please use H4 (####) or H5 (#####) section names.'
                      '\n\tDetails: %s' % maxh3)


def _check_writeup(writeup_file):
    writeup = writeup_file.read()

    # Check if writeup.md starts with the challenge name in H1 style.
    config = read_config('config.yml')
    config_challenge_name = config['name']

    h1_pattern = '^%s\n={1,}\n\n' % (config_challenge_name)
    try:
        writeup_challenge_name = re.search('^.*\n', writeup).group()[:-1]
        assert writeup_challenge_name == config_challenge_name
        re.search(h1_pattern, writeup).group()
    except AssertionError:
        logging.error('The challenge name in writeup.md (%s) and config.yml (%s) have to be the same.' %
                      (writeup_challenge_name, config_challenge_name))
    except Exception:
        logging.error('The challenge names in writeup.md have to be in H1 style. For example: \n\n'
                      'Challenge name\n'
                      '==============\n')

    # Check if costs and H2 sections are correct in writeup.md
    cost_pattern = '\nCost: [0-9]{1,2}%\n'
    costs = [int(re.search(r'[0-9]{1,2}', cost).group())
             for cost in re.findall(cost_pattern, writeup)]
    if sum(costs) != COST_SUM:
        logging.error('The sum of costs in writeup.md should be %d%%. \n \tPlease make sure you have the following'
                      ' format (take care of the white spaces, starting and ending newlines):\n %s' % (COST_SUM,
                                                                                                       cost_pattern))

    h2_pattern = r'\n## [A-Z].{5,150}\n'
    h2 = re.findall(h2_pattern, writeup)

    if len(h2) < MIN_WRITEUP_SECTIONS:
        logging.error('There should be at least %d sections in writeup.md' % MIN_WRITEUP_SECTIONS)

    h2_last = h2.pop()
    if h2_last != '\n## Complete solution\n':
        logging.error('The last section should be called "Complete solution" in writeup.md')

    h2costs = [re.search(h2_pattern, h2cost).group()
               for h2cost in re.findall(h2_pattern + cost_pattern, writeup)]

    missing_costs = set(h2) - set(h2costs)
    if len(missing_costs) > 0:
        logging.error('No cost is defined in writeup.md for section(s): %s \n \tPlease make sure you have the following'
                      ' format (take care of the starting and ending newlines):\n %s' % (missing_costs, cost_pattern))


def _http_request(url):
    # Check controller's test endpoint.
    curl = ['curl', '-s']

    if url[-4:] == 'test':
        method = 'GET'
        func = 'Test'
    else:
        method = 'POST'
        func = 'Solution checker'
        curl += ['-X POST']

    curl += [url]

    try:
        output = subprocess.check_output(curl).decode('utf-8')
        logging.info('%s output: \n\n%s' % (func, output))

        # Check if solution checker returns a well-formatted JSON response
        solved = False
        if func == 'Solution checker':
            response = json.loads(output)
            solved = bool(response['solved'])
            assert isinstance(response['message'], str)

    except subprocess.CalledProcessError as e:
        logging.error('Curl returned with error. \n\tDetails: %s' % e)

    except OSError as e:
        logging.error('Curl returned with error. \n\tDetails: %s' % e)

    except ValueError as e:
        logging.error('Could not parse the response of solution checker. Bad JSON format. \n\tDetails: %s' % e)

    except NameError as e:
        logging.error('JSON attribute "solved" must be a boolean value. \n\tDetails: %s' % e)

    except KeyError as e:
        if e.args[0] == 'solved':
            logging.error('JSON key "%s" is missing from solution checker response. ' % e.args[0])
        elif e.args[0] == 'message':
            logging.warning('JSON key "%s" is missing from solution checker response. \n\t  In certain cases '
                            '(e.g., programming challenges) messages help users to complete the challenge.' % e.args[0])
    except AssertionError:
        logging.error('JSON attribute "message" must be a string value.')

    else:

        if (re.search('OK', output) and func == 'Test') or solved:
            logging.info('%s endpoint passed!' % func)
        elif re.search('404', output) and func == 'Test':
            logging.warning('Test endpoint is not found. '
                            '\n\t  Please implement it so as to make sure your challenge is working correctly.')
        elif re.search('405', output):
            logging.error('%s endpoint should implement HTTP %s.' % (func, method))
        elif re.search('500', output) or not solved:
            logging.error('%s endpoint failed!' % func)


if __name__ == '__main__':
    """
    Sanity check and test an avatao challenge repository. Simply add the challenge repository path as
    the first argument and the script does the rest.

    Python dependencies:
        - PyYAML (http://pyyaml.org/) or simply `pip3 install PyYAML`
          (on Ubuntu you additionally need `apt-get install python3-yaml`)
    """
    init_logger()

    repo_path, repo_name = get_sys_args()
    os.chdir(repo_path)
    sanity_check()

    logging.info('Finished')
