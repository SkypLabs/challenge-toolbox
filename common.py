import glob
import logging
import os
import posixpath
import subprocess
import sys

import yaml

DOCKER_REGISTRY = os.getenv('DOCKER_REGISTRY', 'eu.gcr.io/avatao-challengestore')
DEFAULT_TIMEOUT = 60 * 15  # timeout for commands


def find_repo_path(base: str) -> str:
    """
    Find the first repo path (parent of config.yml) in a base directory

    :param base: base directory
    :return: the repo path
    """
    if os.path.exists(os.path.join(base, 'config.yml')):
        return base

    for item in glob.glob(os.path.join(base, '**', 'config.yml'), recursive=True):
        return os.path.dirname(item)

    logging.info('Could not find a repository in %s' % base)
    sys.exit(1)


def get_sys_args() -> tuple:
    """
    Get parsed command line arguments

    Absolute repository path and the docker repository name (optional)
    :return tuple: repo_path, repo_name 
    """
    if os.getenv('DRONE', '').lower() in ('true', '1'):
        return os.environ['DRONE_WORKSPACE'], os.environ['DRONE_REPO_NAME']

    if not 2 <= len(sys.argv) <= 3:
        logging.info('Usage: %s <git_repo_path> [docker_repo_name]' % sys.argv[0])
        sys.exit(1)

    repo_path = find_repo_path(os.path.realpath(sys.argv[1]))
    repo_name = sys.argv[2] if len(sys.argv) >= 3 else os.path.basename(repo_path)

    return repo_path, repo_name


def get_image_url(repo_name: str, short_name: str=None) -> str:
    """
    Return an absolute docker image URL

    :param repo_name: name of the docker repository (challenge name)
    :param short_name: [optional] e.g. solvable or controller
    :return: absolute URL
    """
    if short_name is not None:
        repo_name = ':'.join((repo_name, short_name))

    return posixpath.join(DOCKER_REGISTRY, repo_name)


def yield_dockerfiles(repo_path: str, repo_name: str):
    """
    Yield (Dockerfile, image_url) pairs from the given repo_path

    :param repo_path: the repo path (parent of config.yml)  
    :param repo_name: name of the docker repository (challenge name)
    """
    for dockerfile in glob.glob(os.path.join(repo_path, '*', 'Dockerfile')):
        short_name = os.path.basename(os.path.dirname(dockerfile))
        image = get_image_url(repo_name, short_name)
        yield dockerfile, image


def read_config(path: str='config.yml') -> dict:
    """
    Read the config.yml file

    :param path: [optional] path to the file or the base directory
    :return: dict
    """
    if os.path.isdir(path):
        path = os.path.join(path, 'config.yml')

    try:
        with open(path, 'r') as f:
            return yaml.safe_load(f)

    except OSError as e:
        logging.error(e)
        sys.exit(1)


def run_cmd(args: list, timeout: int=DEFAULT_TIMEOUT, **kwargs) -> int:
    """
    Run the given command with subprocess.check_call

    :param args: list of args for Popen
    :param timeout: [optional] process timeout (defaults to DEFAULT_TIMEOUT)
    :param kwargs: [optional] additional key arguments
    :raise subprocess.CalledProcessError
    :return int
    """
    logging.debug('Running %s ...' % ' '.join(map(str, args)))
    return subprocess.check_call(args, timeout=timeout, **kwargs)


def init_logger() -> None:
    """
    Configure the default python logger
    """
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('[%(levelname)s] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
